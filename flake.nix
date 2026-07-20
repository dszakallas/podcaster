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

        podcaster = pkgs.callPackage ./nix/package.nix {
          inherit pyproject-nix uv2nix pyproject-build-systems;
        };

        dockerImage = pkgs.callPackage ./nix/dockerImage.nix {
          inherit podcaster ai-sdk-anthropic;
        };
      in
      {
        packages = {
          default = podcaster;
          podcaster = podcaster;
          dockerImage = dockerImage;
          ai-sdk-anthropic = ai-sdk-anthropic;
        };

        dockerImages = {
          default = dockerImage;
          podcaster = dockerImage;
        };
      }
    );
}
