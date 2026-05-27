export const MODEL_COLORS = [
    '#5acce6', '#6fe5b8', '#a78bfa', '#f59e0b',
    '#f472b6', '#34d399', '#fb923c', '#60a5fa',
];
export const METRIC_KEYS = ['Accuracy', 'AUC', 'F1', 'Precision', 'Recall'];

export const fmt = (v: number | null | undefined, d = 3) => v == null ? '—' : v.toFixed(d);
export const pct = (v: number | null | undefined) => v == null ? '—' : `${(v * 100).toFixed(1)}%`;
