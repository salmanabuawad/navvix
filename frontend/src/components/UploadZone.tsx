import { useId, useState, DragEvent } from 'react';
import { UploadCloud } from 'lucide-react';

interface Props {
  onUpload: (file: File) => void;
  disabled?: boolean;
}

export function UploadZone({ onUpload, disabled }: Props) {
  const inputId = useId();
  const [dragging, setDragging] = useState(false);

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const file = files[0];
    const ext  = file.name.split('.').pop()?.toLowerCase();
    if (ext !== 'dxf' && ext !== 'dwfx') {
      alert('Please select a .dxf or .dwfx file.');
      return;
    }
    onUpload(file);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (!disabled) handleFiles(e.dataTransfer.files);
  }

  return (
    <label
      htmlFor={inputId}
      onDragOver={e => { e.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={[
        'flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-8 select-none transition-colors',
        dragging                  ? 'border-accent bg-blue-50'      :
        disabled                  ? 'border-gray-200 bg-gray-50 cursor-not-allowed opacity-60' :
                                    'border-border bg-white hover:border-accent hover:bg-blue-50/40 cursor-pointer',
      ].join(' ')}
    >
      <UploadCloud className="w-10 h-10 text-accent" strokeWidth={1.5} />
      <div className="text-center">
        <p className="font-semibold text-text text-sm">
          {disabled ? 'Uploading…' : 'Drop DXF file here'}
        </p>
        <p className="text-xs text-muted mt-0.5">or click to browse — .dxf / .dwfx</p>
      </div>
      <input
        id={inputId}
        type="file"
        accept=".dxf,.dwfx"
        className="hidden"
        onChange={e => { handleFiles(e.target.files); e.target.value = ''; }}
        disabled={disabled}
      />
    </label>
  );
}
