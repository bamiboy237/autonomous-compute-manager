// Package version reports the acm-edge build version.
package version

// Version is overridden at build time with -ldflags "-X .../version.Version=...".
var Version = "0.1.0-dev"

// String returns the human-readable version banner.
func String() string {
	return "acm-edge " + Version
}
