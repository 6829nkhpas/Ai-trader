fn main() {
    println!("cargo:rerun-if-changed=../shared_protos/market_data.proto");
    
    // Use vendored protoc compiler
    std::env::set_var("PROTOC", protoc_bin_vendored::protoc_bin_path().unwrap());

    prost_build::Config::new()
        .compile_protos(&["../shared_protos/market_data.proto"], &["../shared_protos"])
        .unwrap();
}