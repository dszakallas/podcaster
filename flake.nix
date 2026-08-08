{
  description = "Podcaster automation tools";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        ai-sdk-anthropic = pkgs.callPackage ./nix/ai-sdk-anthropic { };

        opencode =
          # avoid AVX/AVX2 is on x86_64, older CPUs do not support it
          if system == "x86_64-linux" then
            let
              bun-baseline = pkgs.bun.overrideAttrs (old: {
                src = pkgs.fetchurl {
                  url = "https://github.com/oven-sh/bun/releases/download/bun-v${old.version}/bun-linux-x64-baseline.zip";
                  hash = "sha256-nYokKSpwaAkCBdqsCloiP19pc29Sh+N7+I07QDHtx1A=";
                };
              });
            in
            pkgs.opencode.override {
              bun = bun-baseline;
            }
          else
            pkgs.opencode;

        podcaster = pkgs.callPackage ./nix/podcaster.nix {
          inherit pyproject-nix uv2nix pyproject-build-systems;
        };

        dockerImageWithOpenCode = pkgs.callPackage ./nix/podcaster-docker {
          inherit podcaster ai-sdk-anthropic opencode;
          name = "podcaster-opencode";
          withAgents = [
            "opencode"
          ];
        };

        dockerImage = pkgs.callPackage ./nix/podcaster-docker {
          inherit podcaster ai-sdk-anthropic;
          withAgents = null;
        };
      in
      {
        packages = {
          default = podcaster;
          podcaster = podcaster;
          ai-sdk-anthropic = ai-sdk-anthropic;
        };

        dockerImages = {
          default = dockerImage;
          podcaster = dockerImage;
          podcaster-opencode = dockerImageWithOpenCode;
        };
      }
    );
}
