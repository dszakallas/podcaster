{
  buildNpmPackage,
  fetchNpmDeps,
  fetchurl,
  jq,
}:

buildNpmPackage rec {
  pname = "ai-sdk-anthropic";
  version = "4.0.16";

  src = fetchurl {
    url = "https://registry.npmjs.org/@ai-sdk/anthropic/-/anthropic-${version}.tgz";
    hash = "sha512-vyH4D6Auih5H2xvVzzh2ep5pbdWiaV7JDC+jHUE7zZJ5Kyv0TteLav4DrOgHzRuyv8ptfUSqFF6Y8//f/Ec0fQ==";
  };

  postPatch = ''
    cp ${./package-lock.json} package-lock.json
    jq 'del(.devDependencies)' package.json > package.json.tmp && mv package.json.tmp package.json
  '';

  nativeBuildInputs = [ jq ];

  npmDeps = fetchNpmDeps {
    inherit src postPatch;
    name = "${pname}-${version}-npm-deps";
    hash = "sha256-T9e64cNXfH7nNjdc7jb40AoUvz4Z3g0bi2khqhHoZYk=";
    nativeBuildInputs = [ jq ];
  };

  dontNpmBuild = true;
}
