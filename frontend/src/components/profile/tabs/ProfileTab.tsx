'use client';

import React, { useState } from 'react';
import { Loader2, ArrowRight, Save, Pencil } from 'lucide-react';
import IdentityHeader from './profile/IdentityHeader';
import { useAuthStore } from '../../../store/useAuthStore';
import { dashboardUrl, openExternalUrl } from '../../../lib/redirect';
import type { AuthUser } from '../../../store/useAuthStore';


interface ProfileTabProps {
  user: AuthUser | null;
  planName: string | null;
  formatDate: (date: string | number) => string;
}

export default function ProfileTab({
  user,
  planName,
  formatDate,
}: ProfileTabProps) {
  const updateName = useAuthStore((s) => s.updateName);
  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState(user?.name ?? '');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const startEdit = () => {
    setDraftName(user?.name ?? '');
    setSaveError(null);
    setEditingName(true);
  };

  const cancelEdit = () => {
    setEditingName(false);
    setSaveError(null);
  };

  const saveName = async () => {
    const trimmed = draftName.trim();
    if (!trimmed || trimmed.length < 3 || trimmed === user?.name) {
      setEditingName(false);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await updateName(trimmed);
      setEditingName(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to update name');
    } finally {
      setSaving(false);
    }
  };

  const handleManageAccount = () => openExternalUrl(dashboardUrl());

  return (
    <div className="space-y-6 flex flex-col h-full overflow-y-auto pr-1 scrollbar-none">
      {/* Account Identity Header with inline name editing */}
      <div className="border-b border-border-default pb-4">
        <IdentityHeader user={user} planName={planName} formatDate={formatDate} />

        {/* Inline name edit + Manage Account redirect */}
        <div className="mt-4 flex flex-col gap-2">
          <span className="text-[10px] uppercase tracking-widest text-text-secondary">Display Name</span>
          {editingName ? (
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                minLength={3}
                maxLength={64}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') saveName();
                  if (e.key === 'Escape') cancelEdit();
                }}
                className="flex-1 rounded-none border border-emerald-500/40 bg-elevated px-3 py-1.5 text-sm text-text-primary outline-none focus:border-emerald-500"
                placeholder="Enter your name"
              />
              <button
                onClick={saveName}
                disabled={saving}
                className="flex items-center gap-1 rounded-none bg-emerald-500/10 border border-emerald-500/30 px-3 py-1.5 text-xs font-bold text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50 transition-all"
              >
                {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                Save
              </button>
              <button
                onClick={cancelEdit}
                disabled={saving}
                className="rounded-none border border-border-default bg-elevated px-3 py-1.5 text-xs font-bold text-text-secondary hover:bg-elevated disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-text-primary">{user?.name || '—'}</span>
                <button
                  onClick={startEdit}
                  className="flex items-center gap-1 rounded-none border border-border-default bg-elevated px-2 py-0.5 text-[10px] font-semibold text-text-secondary hover:bg-elevated hover:text-text-primary"
                  title="Edit display name"
                >
                  <Pencil size={11} />
                  Edit
                </button>
              </div>
              <button
                onClick={handleManageAccount}
                className="flex items-center gap-2 rounded-none border border-border-default bg-elevated hover:bg-elevated hover:text-text-primary px-3 py-1.5 text-xs font-bold text-text-secondary transition-all"
              >
                <span>MANAGE ACCOUNT</span>
                <ArrowRight size={12} />
              </button>
            </div>
          )}
          {saveError && <p className="text-[11px] text-rose-400 mt-1">{saveError}</p>}
        </div>
      </div>

      {/* Membership Info Footer */}
      <div className="border-t border-border-default pt-4 flex flex-col space-y-2 shrink-0">
        <div className="flex justify-between items-center py-1.5">
          <span className="text-[10px] uppercase tracking-widest text-text-secondary">Strat AI Plan</span>
          <span className="text-xs font-black text-text-primary uppercase">
            {planName && planName !== 'none' ? `${planName} EDITION` : 'STARTER EDITION'}
          </span>
        </div>
        <div className="flex justify-between items-center py-1.5 border-t border-border-default">
          <span className="text-[10px] uppercase tracking-widest text-text-secondary">Account Email</span>
          <span className="text-xs font-semibold text-text-primary">{user?.email || '—'}</span>
        </div>
      </div>
    </div>
  );
}
