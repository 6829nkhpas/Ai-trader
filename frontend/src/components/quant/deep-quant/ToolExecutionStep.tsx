import React from 'react';
import {
  CheckCircle2,
  Loader2,
  Terminal,
  Activity,
  BarChart3,
  TrendingUp,
  Shapes,
  ChevronsUpDown,
  Newspaper,
  Brain,
  Compass,
  Scale,
  Workflow,
  Layers,
  Wrench,
  AlertTriangle,
  SlidersHorizontal,
  LineChart,
  Gauge,
  PlusCircle,
} from 'lucide-react';
import { ReasoningStep } from '../../../store/useQuantStore';
import { highlightNumbers } from './textHighlighter';

interface ToolExecutionStepProps {
  step: ReasoningStep;
  reasoningSteps: ReasoningStep[];
  sessionStatus: string;
}

function getToolIcon(toolName: string | undefined) {
  const name = (toolName || '').toLowerCase().replace(/_/g, ' ');
  if (name.includes('session') || name.includes('context')) {
    return <Terminal size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('candles')) {
    return <Activity size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('consensus')) {
    return <BarChart3 size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('multi tf') || name.includes('trend')) {
    return <TrendingUp size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('patterns')) {
    return <Shapes size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('support') || name.includes('resistance')) {
    return <ChevronsUpDown size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('news')) {
    return <Newspaper size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('prediction')) {
    return <Brain size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('regime')) {
    return <Compass size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('strength')) {
    return <Scale size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('flow') || name.includes('order')) {
    return <Workflow size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('options') || name.includes('analytics')) {
    return <Layers size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('event') || name.includes('risk')) {
    return <AlertTriangle size={12} className="text-text-muted shrink-0 text-amber-500/80" />;
  }
  if (name.includes('volume') || name.includes('profile')) {
    return <SlidersHorizontal size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('forecast')) {
    return <LineChart size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('performance') || name.includes('track record')) {
    return <Gauge size={12} className="text-text-muted shrink-0" />;
  }
  if (name.includes('declare') || name.includes('trade')) {
    return <PlusCircle size={12} className="text-text-muted shrink-0" />;
  }
  return <Wrench size={12} className="text-text-muted shrink-0" />;
}

export default function ToolExecutionStep({
  step,
  reasoningSteps,
  sessionStatus,
}: ToolExecutionStepProps) {
  if (step.type !== 'tool_start') return null;

  // sequential counting to match tool_start to tool_end
  const stepIdx = reasoningSteps.indexOf(step);
  const startsUpToHere = reasoningSteps
    .slice(0, stepIdx + 1)
    .filter((s) => s.type === 'tool_start' && s.toolName === step.toolName).length;
  const endsAfterHere = reasoningSteps
    .slice(stepIdx + 1)
    .filter((s) => s.type === 'tool_end' && s.toolName === step.toolName).length;

  const runSettled = sessionStatus !== 'running';
  const isCompleted = endsAfterHere >= startsUpToHere || runSettled;
  const formattedToolName = step.toolName ? step.toolName.replace(/_/g, ' ') : '';

  const borderClass = isCompleted
    ? 'border border-emerald-500/15 bg-gradient-to-r from-emerald-500/5 via-elevated/20 to-elevated/5'
    : 'border border-amber-500/15 bg-gradient-to-r from-amber-500/5 via-elevated/35 to-elevated/10';

  return (
    <div className="flex justify-start animate-fade-in font-sans pl-1 w-full my-2 select-text">
      <div className={`rounded px-3 py-2.5 text-[10px] leading-relaxed shadow-md w-full ${borderClass}`}>
        <div className="flex items-center justify-between gap-2 font-sans select-none text-text-primary w-full">
          <div className="flex items-center gap-1.5">
            {getToolIcon(step.toolName)}
            <span className={`text-[11px] font-extrabold capitalize ${isCompleted ? 'text-text-primary' : 'text-amber-500'}`}>
              {formattedToolName}
            </span>
          </div>
          {isCompleted ? (
            <CheckCircle2 size={13} className="text-emerald-500 shrink-0" />
          ) : (
            <Loader2 size={13} className="animate-spin text-amber-500 shrink-0" />
          )}
        </div>

        {step.args && Object.keys(step.args).length > 0 && (
          <div className="mt-2 ml-[18px] pl-2 border-l border-border-default/30 text-[8.5px] text-text-secondary leading-normal font-sans space-y-0.5">
            {Object.entries(step.args).map(([k, v]) => (
              <div key={k} className="flex gap-1.5 items-start">
                <span className="text-text-muted font-semibold shrink-0">{k}:</span>
                <span className="text-text-secondary font-sans break-all">
                  {highlightNumbers(JSON.stringify(v))}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
