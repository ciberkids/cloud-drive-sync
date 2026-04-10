import { useStatus } from "../lib/hooks";

export function About() {
  const status = useStatus();
  const version = status.daemon?.version ?? null;
  const buildDate = status.daemon?.build_date ?? null;

  return (
    <div className="about-page">
      <div className="about-header">
        <img src="/cloud-drive-sync.svg" alt="Cloud Drive Sync" className="about-icon" />
        <div>
          <h2>Cloud Drive Sync</h2>
          {version && <span className="about-version">v{version}</span>}
          {buildDate && <span className="about-build-date">Build date: {buildDate}</span>}
        </div>
      </div>

      <p className="about-description">
        Open-source cloud sync client for Linux, macOS, and Windows.
        Sync your local folders with Google Drive (and more providers) without
        subscriptions, accounts, or hidden fees.
      </p>

      <div className="about-badges">
        <div className="about-badge">
          <span className="about-badge-icon">&#x2705;</span>
          <span>No advertisements</span>
        </div>
        <div className="about-badge">
          <span className="about-badge-icon">&#x1F512;</span>
          <span>No tracking or telemetry</span>
        </div>
        <div className="about-badge">
          <span className="about-badge-icon">&#x1F513;</span>
          <span>Free &amp; open source</span>
        </div>
      </div>

      <div className="about-support">
        <p>If you find this useful, consider supporting development:</p>
        <a
          href="https://buymeacoffee.com/ciberkids"
          target="_blank"
          rel="noopener noreferrer"
        >
          <img
            src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png"
            alt="Buy Me A Coffee"
            className="bmc-button-img"
          />
        </a>
      </div>

      <div className="about-links">
        <a
          href="https://github.com/ciberkids/cloud-drive-sync"
          target="_blank"
          rel="noopener noreferrer"
        >
          GitHub
        </a>
        <span className="about-links-sep">·</span>
        <a
          href="https://github.com/ciberkids/cloud-drive-sync/issues"
          target="_blank"
          rel="noopener noreferrer"
        >
          Report an issue
        </a>
      </div>
    </div>
  );
}
