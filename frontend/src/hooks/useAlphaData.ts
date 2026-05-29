import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { useAuthStore } from '../store/useAuthStore';

const BASE_URL = 'http://localhost:3001/api';

interface MarginData {
  equity?: {
    enabled: boolean;
    net: number;
    available: {
      cash: number;
      collateral: number;
      intraday_payback: number;
      ad_hoc: number;
    };
    utilised: {
      debits: number;
      m2m: number;
      m2m_unrealised: number;
      m2m_realised: number;
      exposure: number;
      option_premium: number;
      additional: number;
    };
  };
  commodity?: any;
}

export function useMargins() {
  const token = useAuthStore((s) => s.token);
  const [data, setData] = useState<MarginData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMargins = useCallback(async () => {
    if (!token) {
      setError('Not authenticated');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get(`${BASE_URL}/portfolio/margins`, {
        headers: {
          Authorization: `Bearer ${token}`
        }
      });
      setData(response.data.margins || null);
    } catch (err: any) {
      console.error('[useMargins] failed to fetch margins:', err);
      const errMsg = err.response?.data?.error || err.message || 'Failed to fetch margins';
      setError(errMsg);
      if (err.response?.status === 403) {
        useAuthStore.getState().setBrokerConnected(false);
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchMargins();
  }, [fetchMargins]);

  return { data, loading, error, refetch: fetchMargins };
}

interface PositionItem {
  tradingsymbol: string;
  exchange: string;
  instrument_token: number;
  product: string;
  quantity: number;
  overnight_quantity: number;
  multiplier: number;
  average_price: number;
  close_price: number;
  last_price: number;
  value: number;
  pnl: number;
  m2m: number;
  realised: number;
  unrealised: number;
  buy_quantity: number;
  buy_price: number;
  buy_value: number;
  sell_quantity: number;
  sell_price: number;
  sell_value: number;
}

interface PositionsPayload {
  net: PositionItem[];
  day: PositionItem[];
}

export function usePositions() {
  const token = useAuthStore((s) => s.token);
  const [data, setData] = useState<PositionsPayload | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPositions = useCallback(async () => {
    if (!token) {
      setError('Not authenticated');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get(`${BASE_URL}/portfolio/positions`, {
        headers: {
          Authorization: `Bearer ${token}`
        }
      });
      setData(response.data.positions || null);
    } catch (err: any) {
      console.error('[usePositions] failed to fetch positions:', err);
      const errMsg = err.response?.data?.error || err.message || 'Failed to fetch positions';
      setError(errMsg);
      if (err.response?.status === 403) {
        useAuthStore.getState().setBrokerConnected(false);
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchPositions();
  }, [fetchPositions]);

  return { data, loading, error, refetch: fetchPositions };
}

interface OrderItem {
  order_id: string;
  status: string;
  tradingsymbol: string;
  exchange: string;
  transaction_type: string;
  quantity: number;
  average_price: number;
  price: number;
  status_message: string | null;
  order_timestamp: string;
  product: string;
  order_type: string;
}

export function useOrderBook() {
  const token = useAuthStore((s) => s.token);
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOrders = useCallback(async () => {
    if (!token) {
      setError('Not authenticated');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get(`${BASE_URL}/portfolio/orders`, {
        headers: {
          Authorization: `Bearer ${token}`
        }
      });
      setOrders(response.data.orders || []);
    } catch (err: any) {
      console.error('[useOrderBook] failed to fetch orders:', err);
      const errMsg = err.response?.data?.error || err.message || 'Failed to fetch order book';
      setError(errMsg);
      if (err.response?.status === 403) {
        useAuthStore.getState().setBrokerConnected(false);
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchOrders();
  }, [fetchOrders]);

  return { orders, loading, error, refetch: fetchOrders };
}
