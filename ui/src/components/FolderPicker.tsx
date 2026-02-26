import { open } from "@tauri-apps/plugin-dialog";

interface FolderPickerProps {
  value: string;
  onChange: (path: string) => void;
  label?: string;
}

export function FolderPicker({
  value,
  onChange,
  label = "Local folder",
}: FolderPickerProps) {
  const handlePick = async () => {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "Select sync folder",
    });
    if (selected && typeof selected === "string") {
      onChange(selected);
    }
  };

  return (
    <div className="folder-picker">
      {label && <label className="field-label">{label}</label>}
      <div className="folder-picker-row">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="/home/user/Google Drive"
          className="input"
        />
        <button onClick={handlePick} className="btn btn-secondary" type="button">
          Browse
        </button>
      </div>
    </div>
  );
}
