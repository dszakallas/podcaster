{
  buildNpmPackage,
  fetchNpmDeps,
  fetchurl,
  jq,
}:

buildNpmPackage rec {
  pname = "ai-sdk-anthropic";
  version = "4.0.46";

  src = fetchurl {
    url = "https://registry.npmjs.org/@ai-sdk/anthropic/-/anthropic-${version}.tgz";
    hash = "sha512-/q/wWLArkavQHeOYdv8kZyJRTuasTRwrzoAnvUaY0sHx/BGDYDWW9ENn7lu/H5iUeYId6NhtYAVv+TlqHpp9Cg==";
  };

  postPatch = ''
    cp ${./package-lock.json} package-lock.json
    jq 'del(.devDependencies)' package.json > package.json.tmp && mv package.json.tmp package.json
  '';

  nativeBuildInputs = [ jq ];

  npmDeps = fetchNpmDeps {
    inherit src postPatch;
    name = "${pname}-${version}-npm-deps";
    hash = "sha256-xwjgqpUr69FD0X5BhGsGKuUF0OxMa2PEwi/jC8VN2io=";
    nativeBuildInputs = [ jq ];
  };

  dontNpmBuild = true;
}
