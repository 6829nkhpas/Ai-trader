import { create } from 'zustand';
import { computeFeatureAccess, type FeatureAccessMap, type FeatureId } from '../lib/featureFlags';
import type { AccessFlags } from '../lib/api/types';

interface FeatureState {
  access: FeatureAccessMap;
  hydrated: boolean;
  setAccessFlags: (flags: AccessFlags | null) => void;
  reset: () => void;
}

const EMPTY_ACCESS: FeatureAccessMap = {
  deepseekGlm: false,
  multiModel: false,
  ghostline: false,
  footprint: false,
  topup: false,
  instantNews: false,
  advanceChart: false,
};

export const useFeatureStore = create<FeatureState>((set) => ({
  access: EMPTY_ACCESS,
  hydrated: false,
  setAccessFlags: (flags) =>
    set({ access: computeFeatureAccess(flags), hydrated: true }),
  reset: () => set({ access: EMPTY_ACCESS, hydrated: false }),
}));

export function useFeature(id: FeatureId): boolean {
  return useFeatureStore((s) => s.access[id]);
}
