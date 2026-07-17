{
  dockerTools,
  buildEnv,
  cacert,
  bash,
  coreutils,
  playwright-driver,
  podcaster,
  ffmpeg,
  rsync,
  rclone,
}:
let
  playwright-browsers = playwright-driver.selectBrowsers {
    withWebkit = true;
    withChromium = false;
    withChromiumHeadlessShell = false;
    withFirefox = false;
    withFfmpeg = true;
  };
in
dockerTools.buildImage {
  name = "podcaster";
  tag = "latest";

  copyToRoot = buildEnv {
    name = "podcaster-image-root";
    paths = [
      podcaster
      cacert
      bash
      coreutils
      ffmpeg
      rsync
      rclone
    ];
    pathsToLink = [
      "/bin"
      "/etc"
    ];
  };

  config = {
    Entrypoint = [ "/bin/podcaster" ];
    Env = [
      "PLAYWRIGHT_BROWSERS_PATH=${playwright-browsers}"
      "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true"
    ];
  };
}
