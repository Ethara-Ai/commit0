use std::fs;
use std::path::{Path, PathBuf};
use std::process;

use clap::Parser;
use walkdir::WalkDir;

use ruststubber::stub_source;

/// Rust function body stubber.
///
/// Replaces function bodies with `panic!("STUB: not implemented")` while
/// preserving test functions, `fn main()`, trait declarations, and
/// `#[cfg(test)]` modules.
#[derive(Parser, Debug)]
#[command(name = "ruststubber", version, about)]
struct Cli {
    /// Input directory containing Rust source files.
    #[arg(long)]
    input_dir: PathBuf,

    /// Output directory for stubbed files. Mutually exclusive with --in-place.
    #[arg(long, conflicts_with = "in_place")]
    output_dir: Option<PathBuf>,

    /// Modify files in place instead of writing to an output directory.
    #[arg(long, conflicts_with = "output_dir")]
    in_place: bool,
}

fn is_in_target_dir(path: &Path) -> bool {
    path.components()
        .any(|c| c.as_os_str() == "target")
}

fn main() {
    let cli = Cli::parse();

    if !cli.in_place && cli.output_dir.is_none() {
        eprintln!("Error: must specify either --output-dir or --in-place");
        process::exit(1);
    }

    let input_dir = &cli.input_dir;
    if !input_dir.is_dir() {
        eprintln!("Error: input directory does not exist: {}", input_dir.display());
        process::exit(1);
    }

    let mut errors = 0u32;
    let mut stubbed = 0u32;
    let mut copied = 0u32;

    for entry in WalkDir::new(input_dir)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let src_path = entry.path();

        if src_path.is_dir() {
            continue;
        }

        // Skip target/ directories (Rust build artifacts).
        let rel_path = src_path.strip_prefix(input_dir).unwrap_or(src_path);
        if is_in_target_dir(rel_path) {
            continue;
        }

        let dest_path = if cli.in_place {
            src_path.to_path_buf()
        } else {
            let out = cli.output_dir.as_ref().unwrap();
            out.join(rel_path)
        };

        if let Some(parent) = dest_path.parent() {
            if !parent.exists() {
                if let Err(e) = fs::create_dir_all(parent) {
                    eprintln!("Error creating directory {}: {e}", parent.display());
                    errors += 1;
                    continue;
                }
            }
        }

        if src_path.extension().and_then(|e| e.to_str()) != Some("rs") {
            if !cli.in_place {
                if let Err(e) = fs::copy(src_path, &dest_path) {
                    eprintln!("Error copying {}: {e}", src_path.display());
                    errors += 1;
                } else {
                    copied += 1;
                }
            }
            continue;
        }

        let source = match fs::read_to_string(src_path) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("Error reading {}: {e}", src_path.display());
                errors += 1;
                continue;
            }
        };

        match stub_source(&source) {
            Ok(output) => {
                if let Err(e) = fs::write(&dest_path, output) {
                    eprintln!("Error writing {}: {e}", dest_path.display());
                    errors += 1;
                } else {
                    stubbed += 1;
                }
            }
            Err(e) => {
                eprintln!("Error parsing {}: {e}", src_path.display());
                errors += 1;
            }
        }
    }

    eprintln!("ruststubber: {stubbed} files stubbed, {copied} files copied, {errors} errors");

    if errors > 0 {
        process::exit(1);
    }
}
