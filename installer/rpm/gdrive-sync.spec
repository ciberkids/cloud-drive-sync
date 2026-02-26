Name:           gdrive-sync
Version:        0.1.0
Release:        1%{?dist}
Summary:        Bidirectional Google Drive sync for Linux
License:        MIT
URL:            https://github.com/gdrive-sync/gdrive-sync

# Disable debug package and automatic dependency detection
%global debug_package %{nil}
AutoReqProv:    no

Source0:        gdrive-sync-daemon
Source1:        gdrive-sync-ui
Source2:        gdrive-sync-daemon.service
Source3:        gdrive-sync-ui.desktop
Source4:        gdrive-sync.desktop
Source5:        gdrive-sync.svg

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
install -Dm755 %{SOURCE0} %{buildroot}%{_bindir}/gdrive-sync-daemon
install -Dm755 %{SOURCE1} %{buildroot}%{_bindir}/gdrive-sync-ui

# Systemd user service
install -Dm644 %{SOURCE2} %{buildroot}%{_userunitdir}/gdrive-sync-daemon.service

# Desktop files
install -Dm644 %{SOURCE3} %{buildroot}%{_sysconfdir}/xdg/autostart/gdrive-sync-ui.desktop
install -Dm644 %{SOURCE4} %{buildroot}%{_datadir}/applications/gdrive-sync.desktop

# Icons
install -Dm644 %{SOURCE5} %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/gdrive-sync.svg

%post
%systemd_user_post gdrive-sync-daemon.service

%preun
%systemd_user_preun gdrive-sync-daemon.service

%postun
%systemd_user_postun_with_restart gdrive-sync-daemon.service

%files
%{_bindir}/gdrive-sync-daemon
%{_bindir}/gdrive-sync-ui
%{_userunitdir}/gdrive-sync-daemon.service
%config(noreplace) %{_sysconfdir}/xdg/autostart/gdrive-sync-ui.desktop
%{_datadir}/applications/gdrive-sync.desktop
%{_datadir}/icons/hicolor/scalable/apps/gdrive-sync.svg

%changelog
* Wed Feb 26 2026 GDrive Sync <noreply@gdrive-sync.dev> - 0.1.0-1
- Initial release
