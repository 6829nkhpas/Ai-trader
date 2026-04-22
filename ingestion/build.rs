// build.rs — Protobuf compilation pipeline for the ingestion service.
//
// This script runs automatically before `cargo build`. It uses `prost-build`
// to compile the `.proto` schemas in `../shared_protos/` into Rust structs
// that are included via `include!(concat!(env!("OUT_DIR"), "/...rs"))` in main.rs.
//
// The compiled output is written to $OUT_DIR (managed by Cargo) and is NOT
// committed to version control (covered by .gitignore's proto-gen exclusion).

fn main() {
    // Point prost-build at the shared protos directory (relative to this crate root).
    // Only market_data.proto is needed for Phase 1.2 — additional protos added later.
    prost_build::compile_protos(
        &["../shared_protos/market_data.proto"],  // Source .proto files to compile
        &["../shared_protos/"],                   // Include path (resolves imports inside protos)
    )
    .expect("Failed to compile protobuf definitions. Ensure protoc is installed and shared_protos/ is accessible.");
}
