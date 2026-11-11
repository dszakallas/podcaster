{
  buildNpmPackage,
  fetchNpmDeps,
  fetchurl,
  jq,
}:

buildNpmPackage rec {
  pname = "ai-sdk-anthropic";
  version = "4.0.102";

  src = fetchurl {
    url = "https://registry.npmjs.org/@ai-sdk/anthropic/-/anthropic-${version}.tgz";
    hash = "sha512-uD7FjOP+f0/S5G38O1Qoi9SMKgHtcKXCLIqsQ5XIxZ8j9MCBRqUc0Rbyt6FZNqiu6r+M+whfunu4CvquE1hO2Q==";
  };

  postPatch = ''
    cp ${./package-lock.json} package-lock.json
    jq 'del(.devDependencies)' package.json > package.json.tmp && mv package.json.tmp package.json
  '';

  nativeBuildInputs = [ jq ];

  npmDeps = fetchNpmDeps {
    inherit src postPatch;
    name = "${pname}-${version}-npm-deps";
    hash = "sha256-RwdRKco6PDJ10TZhUMRwW0et8xN+X8uAu1fJzELBdUk=";
    nativeBuildInputs = [ jq ];
  };

  dontNpmBuild = true;
}
