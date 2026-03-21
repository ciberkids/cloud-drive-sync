Name:           cloud-drive-sync
Version:        0.1.0
Release:        1%{?dist}
Summary:        Bidirectional Google Drive sync for Linux
License:        MIT
URL:            https://github.com/cloud-drive-sync/cloud-drive-sync

# Disable debug package and automatic dependency detection
%global debug_package %{nil}
AutoReqProv:    no

Source0:        cloud-drive-sync-daemon
Source1:        cloud-drive-sync-ui
Source2:        cloud-drive-sync-daemon.service
Source3:        cloud-drive-sync-ui.desktop
Source4:        cloud-drive-sync.desktop
Source5:        cloud-drive-sync.svg

BuildRequires:  systemd-rpm-macros

Requires:       webkit2gtk4.1
Requires:       gtk3
Requires:       libayatana-appindicator-gtk3

%description
A Linux-native bidirectional Google Drive sync solution that runs as a
background daemon with desktop integration via a system tray icon and
settings UI.

%install
# Binaries
install -Dm755 %{SOURCE0} %{buildroot}%{_bindir}/cloud-drive-sync-daemon
install -Dm755 %{SOURCE1} %{buildroot}%{_bindir}/cloud-drive-sync-ui

# Systemd user service
install -Dm644 %{SOURCE2} %{buildroot}%{_userunitdir}/cloud-drive-sync-daemon.service

# Desktop files
install -Dm644 %{SOURCE3} %{buildroot}%{_sysconfdir}/xdg/autostart/cloud-drive-sync-ui.desktop
install -Dm644 %{SOURCE4} %{buildroot}%{_datadir}/applications/cloud-drive-sync.desktop

# Icons
install -Dm644 %{SOURCE5} %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/cloud-drive-sync.svg

%post
%systemd_user_post cloud-drive-sync-daemon.service

%preun
%systemd_user_preun cloud-drive-sync-daemon.service

%postun
%systemd_user_postun_with_restart cloud-drive-sync-daemon.service

%files
%{_bindir}/cloud-drive-sync-daemon
%{_bindir}/cloud-drive-sync-ui
%{_userunitdir}/cloud-drive-sync-daemon.service
%config(noreplace) %{_sysconfdir}/xdg/autostart/cloud-drive-sync-ui.desktop
%{_datadir}/applications/cloud-drive-sync.desktop
%{_datadir}/icons/hicolor/scalable/apps/cloud-drive-sync.svg

%changelog
* Wed Feb 26 2026 Cloud Drive Sync <noreply@cloud-drive-sync.dev> - 0.1.0-1
- Initial release
