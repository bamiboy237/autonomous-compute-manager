// Command acm-edge is the outbound-only site daemon (see edge/README.md).
package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/bamiboy237/autonomous-compute-manager/edge/internal/version"
)

func main() {
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()
	if *showVersion {
		fmt.Println(version.String())
		return
	}
	fmt.Fprintln(os.Stderr, "acm-edge: daemon not implemented yet (THE-37)")
	os.Exit(2)
}
