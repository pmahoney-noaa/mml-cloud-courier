"""PyInstaller entry: mmlcc-service.exe — SCM host AND command line.

Launched by the SCM (bare ImagePath, no arguments) it enters the service
control dispatcher; with arguments it is the install|start|stop|remove|
update command line. Both routes live in windows_service.run()."""

from mml_cloud_courier.service.windows_service import run

if __name__ == "__main__":
    raise SystemExit(run())
