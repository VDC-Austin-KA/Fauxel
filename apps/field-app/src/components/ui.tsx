import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, TextInputProps, View, ViewStyle } from 'react-native';
import { TARGET, useTheme } from '../theme';

export function Btn(p: { label: string; onPress: () => void; kind?: 'primary' | 'danger' | 'plain'; disabled?: boolean; busy?: boolean; style?: ViewStyle }) {
  const t = useTheme();
  const bg = p.kind === 'danger' ? t.danger : p.kind === 'plain' ? 'transparent' : t.primary;
  const fg = p.kind === 'plain' ? t.text : t.onPrimary;
  return (
    <Pressable
      accessibilityRole="button"
      disabled={p.disabled || p.busy}
      onPress={p.onPress}
      style={({ pressed }) => [s.btn, { backgroundColor: bg, borderColor: t.border, opacity: p.disabled ? 0.4 : pressed ? 0.8 : 1 }, p.style]}>
      {p.busy ? <ActivityIndicator color={fg} /> : <Text style={[s.btnText, { color: fg }]}>{p.label}</Text>}
    </Pressable>
  );
}

export function Card({ title, children, right }: { title?: string; children: React.ReactNode; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={[s.card, { backgroundColor: t.card, borderColor: t.border }]}>
      {title ? (
        <View style={s.row}>
          <Text style={[s.h2, { color: t.text }]}>{title}</Text>
          {right}
        </View>
      ) : null}
      {children}
    </View>
  );
}

export function Chip({ label, color }: { label: string; color: string }) {
  return (
    <View style={[s.chip, { backgroundColor: color }]}>
      <Text style={s.chipText}>{label}</Text>
    </View>
  );
}

export function Field(p: TextInputProps & { label: string }) {
  const t = useTheme();
  const { label, ...rest } = p;
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={[s.label, { color: t.sub }]}>{label}</Text>
      <TextInput placeholderTextColor={t.sub} {...rest} style={[s.input, { color: t.text, borderColor: t.border, backgroundColor: t.bg }, rest.style]} />
    </View>
  );
}

export function T({ children, sub, style }: { children: React.ReactNode; sub?: boolean; style?: any }) {
  const t = useTheme();
  return <Text style={[{ color: sub ? t.sub : t.text, fontSize: 16 }, style]}>{children}</Text>;
}

export const s = StyleSheet.create({
  btn: { minHeight: TARGET, paddingHorizontal: 18, borderRadius: 12, borderWidth: 2, alignItems: 'center', justifyContent: 'center', marginVertical: 4 },
  btnText: { fontSize: 18, fontWeight: '700' },
  card: { borderRadius: 14, borderWidth: 2, padding: 14, marginBottom: 14 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  h2: { fontSize: 20, fontWeight: '800' },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
  chipText: { color: '#000', fontWeight: '800', fontSize: 14 },
  label: { fontSize: 14, fontWeight: '700', marginBottom: 4 },
  input: { minHeight: TARGET, borderWidth: 2, borderRadius: 12, paddingHorizontal: 12, fontSize: 18 },
});
