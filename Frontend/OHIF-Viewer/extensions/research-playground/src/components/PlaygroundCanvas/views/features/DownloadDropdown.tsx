import React, { useState, useRef, useEffect } from 'react';
import { Download, ChevronDown, FileSpreadsheet } from 'lucide-react';

interface DownloadDropdownProps {
  csvPaths?: Record<string, string>;
}

export default function DownloadDropdown({ csvPaths }: DownloadDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!csvPaths || Object.keys(csvPaths).length === 0) return null;

  const entries = Object.entries(csvPaths);

  return (
    <div className="relative z-50" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 rounded-lg border border-[#1f2d40] bg-[#0b1015] px-3 py-1.5 text-[11px] font-medium text-[#5acce6] transition-colors hover:bg-[#151a1f]"
      >
        <Download size={12} />
        Export Data
        <ChevronDown size={12} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1 w-64 rounded-xl border border-[#1f2d40] bg-[#0b1015] p-2 shadow-xl">
          <div className="mb-2 px-2 text-[10px] uppercase tracking-wide text-[#566a84]">
            Available Downloads
          </div>
          <div className="flex flex-col gap-1">
            {entries.map(([label, path]) => (
              <a
                key={label}
                href={`/api/workspace/file?path=${encodeURIComponent(path)}`}
                download
                onClick={() => setIsOpen(false)}
                className="flex items-start gap-2 rounded-lg p-2 transition-colors hover:bg-[#151a1f]"
              >
                <FileSpreadsheet size={14} className="mt-0.5 shrink-0 text-[#6fe5b8]" />
                <div className="flex flex-col min-w-0">
                  <span className="truncate text-[11px] font-medium text-[#91b9cd]">{label}</span>
                  <span className="truncate font-mono text-[9px] text-[#3c5d80]">{path.split(/[/\\]/).pop()}</span>
                </div>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
