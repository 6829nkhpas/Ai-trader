'use client';

import React, { useEffect } from 'react';
import TradingChart from '../components/TradingChart';
import TerminalLayout from '../components/layout/TerminalLayout';
import AgentStatusPanel from '../components/panels/AgentStatusPanel';
import LiveFeedPanel from '../components/panels/LiveFeedPanel';
import { useTradeStore } from '../store/useTradeStore';

export default function Home() {
  const { connectWebSocket } = useTradeStore();

  useEffect(() => {
    connectWebSocket();
  }, [connectWebSocket]);

  return (
    <TerminalLayout
      sidebar={
        <>
          <AgentStatusPanel />
          <LiveFeedPanel />
        </>
      }
    >
      <div className="flex h-full min-h-0 w-full flex-col">
        <TradingChart />
      </div>
    </TerminalLayout>
  );
}
