import { useColorScheme } from 'react-native';

export const STATUS = { installed: '#22c55e', inProgress: '#eab308', deviation: '#ef4444', notStarted: '#9ca3af' } as const;

const light = { bg: '#ffffff', card: '#f1f5f9', text: '#0b1220', sub: '#334155', border: '#0b1220', primary: '#0b5fff', onPrimary: '#ffffff', danger: STATUS.deviation };
const dark = { bg: '#000000', card: '#111827', text: '#ffffff', sub: '#cbd5e1', border: '#ffffff', primary: '#5b9bff', onPrimary: '#000000', danger: '#ff6b6b' };

export type Theme = { [K in keyof typeof light]: string };
export const useTheme = (): Theme => (useColorScheme() === 'dark' ? dark : light);

export const TARGET = 56; // glove-friendly minimum touch target (dp)
