import React, { useState, useRef } from 'react';

interface ToolOption {
  id: string;
  label: string;
  icon: React.ElementType;
  shortcut?: string;
}

interface ToolMenuProps {
  icon: React.ElementType;
  isActive: boolean;
  options: ToolOption[];
  onSelect: (id: string) => void;
}

export function ToolMenu({ icon: Icon, isActive, options, onSelect }: ToolMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    setIsOpen(true);
  };

  const handleMouseLeave = () => {
    timeoutRef.current = setTimeout(() => {
      setIsOpen(false);
    }, 150);
  };

  return (
    <div 
      className="relative flex items-center justify-center w-full"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button
        type="button"
        className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${
          isActive
            ? 'text-primary bg-primary/10'
            : isOpen 
              ? 'text-text-primary bg-elevated'
              : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
        }`}
      >
        <Icon size={15} />
      </button>

      {isOpen && (
        <div className="absolute left-[100%] top-0 z-50 ml-1 w-48 rounded-md border border-border-default bg-surface shadow-lg panel-shadow py-1">
          {options.map((option) => {
            const OptionIcon = option.icon;
            return (
              <button
                key={option.id}
                onClick={() => {
                  onSelect(option.id);
                  setIsOpen(false);
                }}
                className="flex w-full items-center gap-3 px-3 py-2 text-sm text-text-secondary hover:bg-elevated hover:text-text-primary text-left"
              >
                <OptionIcon size={14} className="shrink-0" />
                <span className="flex-1 truncate">{option.label}</span>
                {option.shortcut && (
                  <span className="text-[10px] text-text-secondary/60">{option.shortcut}</span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
