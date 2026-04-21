package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func main() {
	targetDir := flag.String("dir", "", "Directory to stub Go files in")
	skipTests := flag.Bool("skip-tests", true, "Skip _test.go files")
	skipVendor := flag.Bool("skip-vendor", true, "Skip vendor/ directory")
	jsonOutput := flag.Bool("json", false, "Output results as JSON")
	flag.Parse()

	stubber := &Stubber{
		SkipTests:  *skipTests,
		SkipVendor: *skipVendor,
	}

	args := flag.Args()
	if len(args) == 1 && strings.HasSuffix(args[0], ".go") {
		result, err := stubber.StubFile(args[0])
		if err != nil {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
			os.Exit(1)
		}
		if *jsonOutput {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			enc.Encode(result)
		} else {
			fmt.Printf("  %s: %d stubbed, %d skipped\n",
				filepath.Base(args[0]), result.Stubbed, result.Skipped)
		}
		return
	}

	dir := *targetDir
	if dir == "" {
		dir = "."
	}

	result, err := stubber.StubDirectory(dir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	if *jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(result)
	} else {
		fmt.Printf("Stubbed %d files (%d functions replaced, %d skipped)\n",
			result.FilesStubbed, result.FunctionsStubbed, result.FunctionsSkipped)
		for _, f := range result.Files {
			if f.Stubbed > 0 {
				fmt.Printf("  %s: %d stubbed, %d skipped\n",
					filepath.Base(f.Path), f.Stubbed, f.Skipped)
			}
		}
	}
}

func isGoSourceFile(path string) bool {
	return strings.HasSuffix(path, ".go") &&
		!strings.HasSuffix(path, "_test.go")
}

func isDocFile(name string) bool {
	return name == "doc.go"
}
