import { UploadCloud } from 'lucide-react';

interface Props {
  onUpload: (file: File) => void;
  disabled?: boolean;
}

export function UploadZone({ onUpload, disabled }: Props) {
  return (
    <label className="block cursor-pointer rounded-2xl border-2 border-dashed border-slate-300 bg-white p-6 text-center hover:border-slate-500">
      <input
        type="file"
        accept=".dxf"
        disabled={disabled}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onUpload(file);
          e.currentTarget.value = '';
        }}
      />
      <UploadCloud className="mx-auto mb-3 h-8 w-8 text-slate-500" />
      <div className="text-sm font-semibold text-slate-900">Upload DXF</div>
      <div className="mt-1 text-xs text-slate-500">Main-plan isolation + semantic dimensions</div>
    </label>
  );
}
