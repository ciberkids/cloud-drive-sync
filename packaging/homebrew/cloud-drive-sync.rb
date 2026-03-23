cask "cloud-drive-sync" do
  version "0.2.0"
  sha256 :no_check  # Updated by CI on release

  url "https://github.com/ciberkids/cloud-drive-sync/releases/download/v#{version}/Cloud.Drive.Sync_#{version}_x64.dmg"
  name "Cloud Drive Sync"
  desc "Multi-cloud bidirectional file sync for Linux, macOS, and Windows"
  homepage "https://github.com/ciberkids/cloud-drive-sync"

  depends_on macos: ">= :catalina"

  app "Cloud Drive Sync.app"

  zap trash: [
    "~/Library/Application Support/cloud-drive-sync",
    "~/Library/Caches/cloud-drive-sync",
    "~/Library/LaunchAgents/com.cloud-drive-sync.daemon.plist",
  ]
end
