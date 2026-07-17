{
  lib,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
  python312,
}:
let
  workspace = uv2nix.lib.workspace.loadWorkspace {
    workspaceRoot = ../.;
  };

  # Create overlay from lockfile
  overlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };

  # Construct python set
  pythonSet =
    (python312.pkgs.callPackage pyproject-nix.build.packages {
      python = python312;
    }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          overlay
        ]
      );
in
pythonSet.mkVirtualEnv "podcaster-env" workspace.deps.default
