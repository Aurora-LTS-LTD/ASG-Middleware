"use client";

/**
 * Recharts wrappers for the M1 finance surfaces. Kept in one place so the
 * rest of the UI never imports recharts directly (swap-friendly) and so the
 * dark-theme styling + ₪ tooltip formatting live in a single spot.
 *
 * All inputs are already-typed M1 payloads; conversions agorot→₪ go through
 * the shared currency util.
 */

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  PieChart,
  Pie,
  Legend,
} from "recharts";
import { formatILS, toMajorUnits } from "@/lib/format/currency";
import type { EarningsPeriod, OrgExpenseCategory } from "@/types/api";

const GRID = "#27272a";
const TICK = { fill: "#71717a", fontSize: 10 };
const TOOLTIP_STYLE = {
  background: "#18181b",
  border: "1px solid #27272a",
  borderRadius: 8,
  fontSize: 12,
} as const;
const PIE_COLORS = [
  "#6366f1", "#10b981", "#3b82f6", "#f59e0b",
  "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444",
];

const kAxis = (v: number) => `₪${Math.round(v / 1000)}k`;

/** Monthly accrued earnings over the last 12 periods (agorot → ₪). */
export function EarningsTrendChart({ periods }: { periods: EarningsPeriod[] }) {
  const data = periods.map((p) => ({
    period: p.period,
    value: toMajorUnits(p.total_amount_minor_units, true),
  }));
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <XAxis dataKey="period" tick={TICK} axisLine={{ stroke: GRID }} tickLine={false} />
        <YAxis tick={TICK} axisLine={false} tickLine={false} width={48} tickFormatter={kAxis} />
        <Tooltip
          cursor={{ fill: "#27272a55" }}
          contentStyle={TOOLTIP_STYLE}
          labelStyle={{ color: "#a1a1aa" }}
          formatter={(value) => [formatILS(Number(value)), "Earnings"]}
        />
        <Bar dataKey="value" fill="#6366f1" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Period income vs expenses (both already in whole ₪). */
export function IncomeExpenseChart({ incomeILS, expenseILS }: { incomeILS: number; expenseILS: number }) {
  const data = [
    { name: "Income", value: incomeILS },
    { name: "Expenses", value: expenseILS },
  ];
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <XAxis dataKey="name" tick={{ fill: "#a1a1aa", fontSize: 11 }} axisLine={{ stroke: GRID }} tickLine={false} />
        <YAxis tick={TICK} axisLine={false} tickLine={false} width={48} tickFormatter={kAxis} />
        <Tooltip cursor={{ fill: "#27272a55" }} contentStyle={TOOLTIP_STYLE} formatter={(value) => [formatILS(Number(value)), ""]} />
        <Bar dataKey="value" radius={[4, 4, 0, 0]}>
          <Cell fill="#10b981" />
          <Cell fill="#f59e0b" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Expenses split by category (agorot → ₪) as a donut. */
export function ExpenseCategoryChart({ categories }: { categories: OrgExpenseCategory[] }) {
  const data = categories.map((c) => ({
    name: c.category,
    value: toMajorUnits(c.total_amount_minor_units, true),
  }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={48} outerRadius={78} paddingAngle={2}>
          {data.map((_, i) => (
            <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} stroke="#18181b" />
          ))}
        </Pie>
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(value, name) => [formatILS(Number(value)), String(name)]} />
        <Legend wrapperStyle={{ fontSize: 11, color: "#a1a1aa" }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
