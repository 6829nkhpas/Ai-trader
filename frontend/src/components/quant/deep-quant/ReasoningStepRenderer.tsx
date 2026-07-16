import React from 'react';
import { Target } from 'lucide-react';
import { ReasoningStep } from '../../../store/useQuantStore';
import { highlightNumbers } from './textHighlighter';

interface ReasoningStepRendererProps {
  step: ReasoningStep;
}

export default function ReasoningStepRenderer({ step }: ReasoningStepRendererProps) {
  if (step.type !== 'message') return null;

  try {
    const jsonMatch = step.content.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      const parsed = JSON.parse(jsonMatch[0]);
      const conviction = parsed.conviction_score ?? parsed.conviction;
      const validation = parsed.setup_validation ?? parsed.validation ?? parsed.setup;
      const plan = parsed.execution_plan ?? parsed.plan;

      if (conviction !== undefined || validation || plan) {
        return (
          <div className="flex justify-start animate-fade-in font-sans w-full select-text my-2">
            <div className="bg-gradient-to-r from-emerald-500/5 via-elevated/40 to-elevated/10 text-text-primary border border-emerald-500/15 rounded px-3 py-2.5 text-[11px] leading-relaxed shadow-md w-full">
              <div className="flex items-center gap-1.5 text-[9px] text-emerald-500 font-bold uppercase tracking-wider mb-2 select-none">
                <Target size={11} className="text-emerald-500 shrink-0" />
                <span>Final Trade Decision</span>
                {conviction !== undefined && (
                  <span className="ml-auto rounded-sm px-1.5 py-0.5 text-[8px] font-black bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                    {conviction}% CONVICTION
                  </span>
                )}
              </div>
              {validation && (
                <p className="text-text-primary mb-1">
                  {highlightNumbers(String(validation))}
                </p>
              )}
              {plan && (
                <p className="text-text-secondary text-[10px] font-sans mt-2 border-t border-border-default/40 pt-2">
                  {highlightNumbers(String(plan))}
                </p>
              )}
            </div>
          </div>
        );
      }
    }
  } catch {
    // JSON parse failed
  }

  return null;
}
