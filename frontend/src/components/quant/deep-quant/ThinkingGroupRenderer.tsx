import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { ReasoningStep } from '../../../store/useQuantStore';
import MarkdownRenderer from './MarkdownRenderer';

interface ThinkingGroupRendererProps {
  steps: ReasoningStep[];
  sessionStatus: string;
}

export default function ThinkingGroupRenderer({
  steps,
  sessionStatus,
}: ThinkingGroupRendererProps) {
  const isRunning = sessionStatus === 'running';
  const [isExpanded, setIsExpanded] = useState(isRunning);
  // Track the isRunning value the expansion state was last synced to, so we
  // can re-sync exactly once when it flips — the "adjust state during
  // render" pattern for deriving state from a prop, instead of a useEffect.
  const [syncedRunning, setSyncedRunning] = useState(isRunning);

  if (isRunning !== syncedRunning) {
    setSyncedRunning(isRunning);
    setIsExpanded(isRunning);
  }

  if (steps.length === 0) return null;

  return (
    <div className="w-full animate-fade-in font-sans">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-1 text-[10px] text-text-muted hover:text-text-primary transition-colors duration-200 select-none focus:outline-none mb-1.5"
      >
        <span>Thinking</span>
        {isExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
      </button>

      {isExpanded && (
        <div className="text-text-secondary text-[11px] leading-relaxed w-full pl-1 space-y-2">
          {steps.map((step) => {
            const cleanContent = step.content.replace(/\{[\s\S]*\}/g, '').trim();
            if (!cleanContent) return null;
            return <MarkdownRenderer key={step.id} content={cleanContent} simple={true} />;
          })}
        </div>
      )}
    </div>
  );
}
