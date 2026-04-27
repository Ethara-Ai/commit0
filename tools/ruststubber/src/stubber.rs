use proc_macro2::TokenStream;
use quote::{quote, ToTokens};
use syn::fold::{self, Fold};
use syn::{
    Block, File, GenericArgument, ImplItemFn, ItemFn, ItemMod, PathArguments, ReturnType,
    TraitItemFn, Type, TypeImplTrait, TypeParamBound,
};

fn has_test_attr(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| a.path().is_ident("test"))
}

fn has_cfg_test_attr(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| {
        a.path().is_ident("cfg") && a.to_token_stream().to_string().contains("test")
    })
}

fn should_preserve_fn(sig: &syn::Signature, attrs: &[syn::Attribute]) -> bool {
    let name = sig.ident.to_string();
    name == "main"
        || sig.constness.is_some()
        || has_test_attr(attrs)
        || has_cfg_test_attr(attrs)
}

fn panic_stub_block() -> Box<Block> {
    Box::new(syn::parse_quote!({ panic!("STUB: not implemented"); }))
}

fn extract_impl_trait(ret: &ReturnType) -> Option<&TypeImplTrait> {
    match ret {
        ReturnType::Type(_, ty) => match ty.as_ref() {
            Type::ImplTrait(it) => Some(it),
            _ => None,
        },
        _ => None,
    }
}

fn first_trait_info(impl_trait: &TypeImplTrait) -> Option<(String, &PathArguments)> {
    for bound in &impl_trait.bounds {
        if let TypeParamBound::Trait(tb) = bound {
            let seg = tb.path.segments.last()?;
            return Some((seg.ident.to_string(), &seg.arguments));
        }
    }
    None
}

fn extract_assoc_type(args: &PathArguments, name: &str) -> Option<Type> {
    if let PathArguments::AngleBracketed(ab) = args {
        for arg in &ab.args {
            if let GenericArgument::AssocType(at) = arg {
                if at.ident == name {
                    return Some(at.ty.clone());
                }
            }
        }
    }
    None
}

/// Build a closure stub for `impl Fn(A, B) -> R` / `impl FnMut(...)` / `impl FnOnce(...)`.
///
/// Generates: `|_: A, _: B| -> R { panic!("STUB: not implemented") }`
fn build_fn_trait_closure(args: &PathArguments) -> Option<Box<Block>> {
    if let PathArguments::Parenthesized(paren) = args {
        let params: Vec<TokenStream> = paren
            .inputs
            .iter()
            .enumerate()
            .map(|(i, ty)| {
                let name = syn::Ident::new(&format!("_{}", i), proc_macro2::Span::call_site());
                quote!(#name: #ty)
            })
            .collect();

        let ret_tokens = match &paren.output {
            ReturnType::Default => quote!(),
            ReturnType::Type(arrow, ty) => quote!(#arrow #ty),
        };

        let closure = quote!({
            panic!("STUB: not implemented");
            #[allow(unreachable_code)]
            |#(#params),*| #ret_tokens { panic!("STUB: not implemented") }
        });

        Some(Box::new(syn::parse2(closure).expect("failed to parse Fn closure stub")))
    } else {
        None
    }
}

fn stub_block_for_impl_trait(impl_trait: &TypeImplTrait) -> Box<Block> {
    if let Some((trait_name, args)) = first_trait_info(impl_trait) {
        match trait_name.as_str() {
            "Iterator" => {
                let item_ty = extract_assoc_type(args, "Item")
                    .unwrap_or_else(|| syn::parse_quote!(()));
                return Box::new(syn::parse_quote!({
                    panic!("STUB: not implemented");
                    #[allow(unreachable_code)]
                    std::iter::empty::<#item_ty>()
                }));
            }
            "IntoIterator" => {
                let item_ty = extract_assoc_type(args, "Item")
                    .unwrap_or_else(|| syn::parse_quote!(()));
                return Box::new(syn::parse_quote!({
                    panic!("STUB: not implemented");
                    #[allow(unreachable_code)]
                    Vec::<#item_ty>::new()
                }));
            }
            "Display" | "Debug" => {
                return Box::new(syn::parse_quote!({
                    panic!("STUB: not implemented");
                    #[allow(unreachable_code)]
                    String::new()
                }));
            }
            "AsRef" => {
                return Box::new(syn::parse_quote!({
                    panic!("STUB: not implemented");
                    #[allow(unreachable_code)]
                    String::new()
                }));
            }
            "Clone" | "Copy" => {
                return Box::new(syn::parse_quote!({
                    panic!("STUB: not implemented");
                    #[allow(unreachable_code)]
                    ()
                }));
            }
            "Future" => {
                let output_ty = extract_assoc_type(args, "Output")
                    .unwrap_or_else(|| syn::parse_quote!(()));
                return Box::new(syn::parse_quote!({
                    panic!("STUB: not implemented");
                    #[allow(unreachable_code)]
                    std::future::ready::<#output_ty>(panic!())
                }));
            }
            "Fn" | "FnMut" | "FnOnce" => {
                if let Some(block) = build_fn_trait_closure(args) {
                    return block;
                }
            }
            _ => {}
        }
    }
    Box::new(syn::parse_quote!({
        panic!("STUB: not implemented");
        #[allow(unreachable_code)]
        loop {}
    }))
}

fn make_stub_block(ret: &ReturnType) -> Box<Block> {
    match extract_impl_trait(ret) {
        Some(impl_trait) => stub_block_for_impl_trait(impl_trait),
        None => panic_stub_block(),
    }
}

/// AST folder that replaces function bodies with `panic!("STUB: not implemented")`.
///
/// Preserves:
/// - `fn main()`
/// - Functions with `#[test]` or `#[cfg(test)]` attributes
/// - Trait function declarations (no bodies)
/// - Everything inside `#[cfg(test)]` modules
pub struct StubFolder;

impl Fold for StubFolder {
    fn fold_item_fn(&mut self, mut i: ItemFn) -> ItemFn {
        if should_preserve_fn(&i.sig, &i.attrs) {
            return fold::fold_item_fn(self, i);
        }
        i.block = make_stub_block(&i.sig.output);
        i
    }

    fn fold_impl_item_fn(&mut self, mut i: ImplItemFn) -> ImplItemFn {
        if should_preserve_fn(&i.sig, &i.attrs) {
            return fold::fold_impl_item_fn(self, i);
        }
        i.block = *make_stub_block(&i.sig.output);
        i
    }

    fn fold_trait_item_fn(&mut self, i: TraitItemFn) -> TraitItemFn {
        i
    }

    fn fold_item_mod(&mut self, i: ItemMod) -> ItemMod {
        if has_cfg_test_attr(&i.attrs) {
            return i;
        }
        fold::fold_item_mod(self, i)
    }
}

pub fn stub_file(file: File) -> File {
    let mut folder = StubFolder;
    folder.fold_file(file)
}

/// Returns `Err` if `syn::parse_file` fails.
pub fn stub_source(source: &str) -> Result<String, String> {
    let parsed = syn::parse_file(source).map_err(|e| format!("syn parse error: {e}"))?;
    let stubbed = stub_file(parsed);
    Ok(prettyplease::unparse(&stubbed))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normal_fn_is_stubbed() {
        let src = r#"
fn add(a: i32, b: i32) -> i32 {
    a + b
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
        assert!(!out.contains("a + b"));
    }

    #[test]
    fn test_test_fn_is_preserved() {
        let src = r#"
#[test]
fn test_something() {
    assert_eq!(1, 1);
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("assert_eq!"));
        assert!(!out.contains(r#"panic!("STUB: not implemented")"#));
    }

    #[test]
    fn test_main_fn_is_preserved() {
        let src = r#"
fn main() {
    println!("hello");
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("println!"));
    }

    #[test]
    fn test_cfg_test_module_preserved() {
        let src = r#"
#[cfg(test)]
mod tests {
    fn helper() -> i32 { 42 }

    #[test]
    fn it_works() {
        assert_eq!(helper(), 42);
    }
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("assert_eq!"));
        assert!(out.contains("42"));
    }

    #[test]
    fn test_impl_method_is_stubbed() {
        let src = r#"
struct Foo;
impl Foo {
    fn bar(&self) -> i32 { 42 }
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
        assert!(!out.contains("42"));
    }

    #[test]
    fn test_trait_decl_unchanged() {
        let src = r#"
trait MyTrait {
    fn do_thing(&self) -> bool;
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("fn do_thing"));
    }

    #[test]
    fn test_impl_iterator_return() {
        let src = r#"
fn get_items() -> impl Iterator<Item = i32> {
    vec![1, 2, 3].into_iter()
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("std::iter::empty"));
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
        assert!(!out.contains("vec!"));
    }

    #[test]
    fn test_impl_iterator_with_lifetime() {
        let src = r#"
fn get_strs<'a>(v: &'a [String]) -> impl Iterator<Item = &'a str> + 'a {
    v.iter().map(|s| s.as_str())
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("std::iter::empty"));
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
    }

    #[test]
    fn test_impl_display_return() {
        let src = r#"
fn display_thing() -> impl std::fmt::Display {
    "hello"
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("String::new()"));
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
    }

    #[test]
    fn test_impl_into_iterator_return() {
        let src = r#"
fn get_collection() -> impl IntoIterator<Item = u8> {
    vec![1u8, 2, 3]
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("Vec"));
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
    }

    #[test]
    fn test_unknown_impl_trait_fallback() {
        let src = r#"
trait Custom {}
fn get_custom() -> impl Custom {
    struct X;
    impl Custom for X {}
    X
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
        assert!(out.contains("loop"));
    }

    #[test]
    fn test_concrete_return_unchanged() {
        let src = r#"
fn get_vec() -> Vec<i32> {
    vec![1, 2, 3]
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
        assert!(!out.contains("std::iter::empty"));
        assert!(!out.contains("loop"));
    }

    #[test]
    fn test_impl_method_with_impl_trait_return() {
        let src = r#"
struct Foo;
impl Foo {
    fn items(&self) -> impl Iterator<Item = String> {
        vec!["a".to_string()].into_iter()
    }
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("std::iter::empty"));
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
    }

    #[test]
    fn test_impl_fn_mut_return() {
        let src = r#"
use std::cmp::Ordering;
struct GridItem;
fn cmp_items(axis: u32) -> impl FnMut(&GridItem, &GridItem) -> Ordering {
    move |a, b| Ordering::Equal
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
        assert!(!out.contains("Ordering::Equal"));
        assert!(!out.contains("loop"));
    }

    #[test]
    fn test_impl_fn_once_no_return() {
        let src = r#"
fn make_callback() -> impl FnOnce(i32) {
    |x| println!("{}", x)
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains(r#"panic!("STUB: not implemented")"#));
        assert!(!out.contains("println"));
        assert!(!out.contains("loop"));
    }

    #[test]
    fn test_const_fn_is_preserved() {
        let src = r#"
struct Styles { header: u8 }
impl Styles {
    pub const fn styled() -> Self {
        Self { header: 42 }
    }
    pub const fn header(mut self, val: u8) -> Self {
        self.header = val;
        self
    }
}
"#;
        let out = stub_source(src).unwrap();
        assert!(out.contains("42"));
        assert!(!out.contains(r#"panic!("STUB: not implemented")"#));
    }
}
