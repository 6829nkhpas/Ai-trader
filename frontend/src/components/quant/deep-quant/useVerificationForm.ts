import { useState, useMemo } from 'react';

export function useVerificationForm(symbol: string, livePrice: number) {
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [entry, setEntry] = useState<string>('');
  const [stopLoss, setStopLoss] = useState<string>('');
  const [takeProfit, setTakeProfit] = useState<string>('');
  const [userAnalysis, setUserAnalysis] = useState<string>('');

  const [hasManuallySetEntry, setHasManuallySetEntry] = useState(false);
  const [hasManuallySetSL, setHasManuallySetSL] = useState(false);
  const [hasManuallySetTP, setHasManuallySetTP] = useState(false);

  // Reset manual inputs when the active symbol changes. This derives state
  // from a prop change, so it's adjusted directly during render (the
  // React-recommended pattern) instead of in a useEffect — it needs to take
  // effect before the stale entry/SL/TP for the old symbol would otherwise
  // render.
  const [prevSymbol, setPrevSymbol] = useState(symbol);
  if (symbol !== prevSymbol) {
    setPrevSymbol(symbol);
    setHasManuallySetEntry(false);
    setHasManuallySetSL(false);
    setHasManuallySetTP(false);
    setUserAnalysis('');
  }

  // Track live price and dynamically pre-fill the entry field, unless the
  // user has manually overridden it. Derived from the `livePrice` prop, so
  // adjusted during render rather than via an effect.
  const [prevLivePrice, setPrevLivePrice] = useState(livePrice);
  if (livePrice !== prevLivePrice) {
    setPrevLivePrice(livePrice);
    if (livePrice > 0 && !hasManuallySetEntry) {
      setEntry(livePrice.toFixed(2));
    }
  }

  // Compute SL/TP based on entry price and side if not manually set. Derived
  // from `entry`/`side`, adjusted during render for the same reason.
  const [prevEntryKey, setPrevEntryKey] = useState(`${entry}|${side}`);
  const entryKey = `${entry}|${side}`;
  if (entryKey !== prevEntryKey) {
    setPrevEntryKey(entryKey);
    const numericEntry = parseFloat(entry);
    if (!isNaN(numericEntry) && numericEntry > 0) {
      if (!hasManuallySetSL) {
        const computedSL = side === 'BUY' ? numericEntry * 0.98 : numericEntry * 1.02;
        setStopLoss(computedSL.toFixed(2));
      }
      if (!hasManuallySetTP) {
        const computedTP = side === 'BUY' ? numericEntry * 1.05 : numericEntry * 0.95;
        setTakeProfit(computedTP.toFixed(2));
      }
    }
  }

  // R:R and % deviations
  const riskToReward = useMemo(() => {
    const e = parseFloat(entry);
    const sl = parseFloat(stopLoss);
    const tp = parseFloat(takeProfit);
    if (isNaN(e) || isNaN(sl) || isNaN(tp) || e <= 0) return null;

    const risk = Math.abs(e - sl);
    const reward = Math.abs(tp - e);
    if (risk <= 0) return null;

    return (reward / risk).toFixed(2);
  }, [entry, stopLoss, takeProfit]);

  const slPercent = useMemo(() => {
    const e = parseFloat(entry);
    const sl = parseFloat(stopLoss);
    if (isNaN(e) || isNaN(sl) || e <= 0) return null;
    return (((sl - e) / e) * 100).toFixed(2);
  }, [entry, stopLoss]);

  const tpPercent = useMemo(() => {
    const e = parseFloat(entry);
    const tp = parseFloat(takeProfit);
    if (isNaN(e) || isNaN(tp) || e <= 0) return null;
    return (((tp - e) / e) * 100).toFixed(2);
  }, [entry, takeProfit]);

  return {
    side,
    setSide,
    entry,
    setEntry,
    setHasManuallySetEntry,
    stopLoss,
    setStopLoss,
    setHasManuallySetSL,
    takeProfit,
    setTakeProfit,
    setHasManuallySetTP,
    userAnalysis,
    setUserAnalysis,
    riskToReward,
    slPercent,
    tpPercent,
  };
}
