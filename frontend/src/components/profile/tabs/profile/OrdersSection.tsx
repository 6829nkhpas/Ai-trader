'use client';

import React, { useState } from 'react';
import { ClipboardList, ChevronUp, ChevronDown, Clock, AlertCircle, Tag } from 'lucide-react';

interface OrdersSectionProps {
  broker: any;
  orders: any[] | undefined;
  formatCurrency: (val: number | undefined) => string;
}

export default function OrdersSection({
  broker,
  orders,
  formatCurrency,
}: OrdersSectionProps) {
  const [expandedOrderId, setExpandedOrderId] = useState<string | null>(null);

  if (!broker || !orders) return null;

  const totalOrders = orders.length;
  const completedOrders = orders.filter((o: any) => o.status === 'COMPLETE').length;
  const rejectedOrders = orders.filter((o: any) => o.status === 'REJECTED').length;

  const toggleExpandOrder = (orderId: string) => {
    setExpandedOrderId(expandedOrderId === orderId ? null : orderId);
  };

  const getOrderStatusClass = (status: string) => {
    switch (status?.toUpperCase()) {
      case 'COMPLETE':
        return 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
      case 'REJECTED':
        return 'bg-rose-500/10 text-rose-400 border border-rose-500/20';
      case 'OPEN':
      case 'PENDING':
        return 'bg-amber-500/10 text-amber-400 border border-amber-500/20';
      default:
        return 'bg-slate-500/10 text-slate-400 border border-slate-500/20';
    }
  };

  return (
    <div className="border border-border-default/40 bg-surface/30 p-4 space-y-3 rounded-none">
      <div className="flex items-center justify-between border-b border-border-default/20 pb-2">
        <div className="flex items-center gap-2">
          <ClipboardList size={14} className="text-emerald-400" />
          <h3 className="text-xs font-black uppercase tracking-wider text-text-primary">Daily Order Execution Log ({totalOrders})</h3>
        </div>
        <div className="flex items-center gap-1.5 text-[9px] font-bold tracking-wider font-mono">
          <span className="text-emerald-400 bg-emerald-500/5 px-1.5 py-0.5 rounded-none border border-emerald-500/10">Done: {completedOrders}</span>
          <span className="text-rose-400 bg-rose-500/5 px-1.5 py-0.5 rounded-none border border-rose-500/10">Rej: {rejectedOrders}</span>
        </div>
      </div>

      <div className="overflow-x-auto border border-border-default/20 rounded-none bg-muted/30 max-h-[260px] scrollbar-thin">
        <table className="w-full text-left text-xs border-collapse">
          <thead className="bg-elevated/80 border-b border-border-default/30 sticky top-0 z-10">
            <tr>
              <th className="w-8 pl-3"></th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Time</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Type</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Symbol</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">Quantity</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">Price</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-center">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-default/10 font-mono text-[11px]">
            {orders.map((order: any, idx: number) => {
              const timeStr = order.order_timestamp ? order.order_timestamp.split(' ')[1] : '--:--:--';
              const isBuy = order.transaction_type?.toUpperCase() === 'BUY';
              const isExpanded = expandedOrderId === order.order_id;
              return (
                <React.Fragment key={order.order_id || idx}>
                  <tr 
                    onClick={() => toggleExpandOrder(order.order_id)}
                    className={`hover:bg-elevated/5 cursor-pointer transition-colors ${isExpanded ? 'bg-elevated/5' : ''}`}
                  >
                    <td className="pl-3 py-2 text-center text-text-muted hover:text-text-primary">
                      {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                    </td>
                    <td className="px-3 py-2 text-text-muted flex items-center gap-1 font-sans">
                      <Clock size={9} className="text-text-muted/65" />
                      <span>{timeStr}</span>
                    </td>
                    <td className="px-3 py-2 font-sans">
                      <span className={`text-[8px] font-bold px-1.5 py-0.2 rounded-none border ${
                        isBuy 
                          ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' 
                          : 'bg-rose-500/10 border-rose-500/20 text-rose-400'
                      }`}>
                        {order.transaction_type}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-sans font-bold text-text-primary">
                      {order.tradingsymbol}
                      <span className="text-[7px] text-text-secondary ml-1 bg-elevated border border-border-default px-1 py-0.2 rounded-none">{order.product}</span>
                    </td>
                    <td className="px-3 py-2 text-right text-text-primary">{order.quantity}</td>
                    <td className="px-3 py-2 text-right text-text-secondary">
                      {order.average_price > 0 ? (order.average_price ?? 0).toFixed(2) : (order.price ?? 0).toFixed(2)}
                    </td>
                    <td className="px-3 py-2 text-center font-sans">
                      <span className={`inline-flex rounded-none px-1.5 py-0.2 text-[8px] font-bold ${getOrderStatusClass(order.status)}`}>
                        {order.status}
                      </span>
                    </td>
                  </tr>

                  {/* Expandable Order Details Drawer */}
                  {isExpanded && (
                    <tr className="bg-surface/40 border-b border-border-default/20">
                      <td colSpan={7} className="p-4">
                        <div className="space-y-3 text-left font-sans">
                          {/* Rejection Banner */}
                          {order.status === 'REJECTED' && order.status_message && (
                            <div className="flex items-start gap-2 rounded-none border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
                              <AlertCircle size={14} className="shrink-0 mt-0.5" />
                              <div className="space-y-0.5">
                                <span className="font-bold block text-[11px]">OMS Rejection Reason:</span>
                                <span className="font-medium leading-relaxed text-[10px]">{order.status_message}</span>
                                {order.status_message_raw && (
                                  <span className="text-[8px] text-rose-500/70 block font-mono mt-1">Raw OMS log: {order.status_message_raw}</span>
                                )}
                              </div>
                            </div>
                          )}

                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[10px] font-sans">
                            {/* Column 1: Order Properties & Route */}
                            <div className="border border-border-default/30 rounded-none bg-muted/50 p-3 space-y-1.5">
                              <span className="text-[8px] font-black uppercase tracking-wider text-text-secondary block border-b border-border-default/30 pb-1 mb-1">
                                ORDER PROPERTIES & ROUTE
                              </span>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Order Variety</span>
                                <span className="font-mono text-text-primary font-semibold uppercase">{order.variety ?? 'regular'}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Order ID</span>
                                <span className="font-mono text-text-primary font-semibold">{order.order_id}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Exchange ID</span>
                                <span className="font-mono text-text-primary font-semibold truncate max-w-[120px]" title={order.exchange_order_id}>
                                  {order.exchange_order_id ?? 'Pending Submission'}
                                </span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Placed By</span>
                                <span className="font-mono text-text-primary font-semibold">{order.placed_by ?? 'Client Terminal'}</span>
                              </div>
                              {order.parent_order_id && (
                                <div className="flex justify-between border-t border-border-default/10 pt-1 mt-0.5">
                                  <span className="text-text-muted">Parent Order ID</span>
                                  <span className="font-mono text-emerald-400 font-semibold">{order.parent_order_id}</span>
                                </div>
                              )}
                            </div>

                            {/* Column 2: Quantity & Slicing Ledgers */}
                            <div className="border border-border-default/30 rounded-none bg-muted/50 p-3 space-y-1.5">
                              <span className="text-[8px] font-black uppercase tracking-wider text-emerald-400 block border-b border-border-default/30 pb-1 mb-1">
                                QUANTITY & SLICING LEDGER
                              </span>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Total Ordered Qty</span>
                                <span className="font-mono text-text-primary font-semibold">{order.quantity}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Filled Quantity</span>
                                <span className="font-mono text-text-primary font-semibold">{order.filled_quantity ?? 0}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Pending Quantity</span>
                                <span className="font-mono text-text-muted font-semibold">{order.pending_quantity ?? 0}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Cancelled Quantity</span>
                                <span className="font-mono text-text-muted font-semibold">{order.cancelled_quantity ?? 0}</span>
                              </div>
                              <div className="flex justify-between border-t border-border-default/20 pt-1 mt-0.5">
                                <span className="text-text-muted">Disclosed Qty</span>
                                <span className="font-mono text-text-primary font-semibold">{order.disclosed_quantity ?? 0}</span>
                              </div>
                              
                              {/* Iceberg metadata slicing */}
                              {order.meta?.iceberg && (
                                <div className="border-t border-border-default/10 pt-1 space-y-0.5">
                                  <span className="text-[7px] font-bold text-text-muted uppercase block">Iceberg Slicing Details</span>
                                  <div className="grid grid-cols-2 gap-x-2 text-[8px] text-text-secondary font-mono">
                                    <span>Leg: {order.meta.iceberg.leg} / {order.meta.iceberg.legs}</span>
                                    <span>Leg Qty: {order.meta.iceberg.leg_quantity}</span>
                                  </div>
                                </div>
                              )}
                            </div>

                            {/* Column 3: Order Pricing & Validity */}
                            <div className="border border-border-default/30 rounded-none bg-muted/50 p-3 space-y-1.5">
                              <span className="text-[8px] font-black uppercase tracking-wider text-rose-400 block border-b border-border-default/30 pb-1 mb-1">
                                PRICING & VALIDITY
                              </span>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Order Type</span>
                                <span className="font-mono text-text-primary font-semibold uppercase">{order.order_type}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Limit Price</span>
                                <span className="font-mono text-text-primary font-semibold">{formatCurrency(order.price)}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Trigger Price</span>
                                <span className="font-mono text-text-primary font-semibold">{formatCurrency(order.trigger_price)}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-text-muted">Average Price</span>
                                <span className="font-mono text-text-primary font-semibold">{formatCurrency(order.average_price)}</span>
                              </div>
                              <div className="flex justify-between border-t border-border-default/20 pt-1 mt-0.5">
                                <span className="text-text-muted">Validity Range</span>
                                <span className="font-mono text-text-primary font-semibold uppercase">
                                  {order.validity} {order.validity_ttl > 0 ? `(${order.validity_ttl}m)` : ''}
                                </span>
                              </div>
                            </div>
                          </div>

                          {/* Audit footer & Tag info */}
                          <div className="border-t border-border-default/25 pt-2 flex flex-col sm:flex-row sm:justify-between gap-1.5 text-[8px] text-text-muted font-mono leading-none">
                            <div className="flex flex-wrap gap-x-3 gap-y-1">
                              <span>OMS Time: {order.order_timestamp ?? 'N/A'}</span>
                              {order.exchange_timestamp && <span>Exchange Time: {order.exchange_timestamp}</span>}
                              {order.exchange_update_timestamp && <span>OMS Update: {order.exchange_update_timestamp}</span>}
                            </div>
                            {order.tag && (
                              <div className="flex items-center gap-0.5 text-emerald-400 font-sans">
                                <Tag size={9} />
                                <span>Tag: {order.tag}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
            {orders.length === 0 && (
              <tr>
                <td colSpan={7} className="py-6 text-center text-text-muted italic font-sans">
                  No order logs registered today.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
