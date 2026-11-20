import React, { useMemo, useRef, useState } from 'react';
import { GestureResponderEvent, LayoutChangeEvent, PanResponder, View } from 'react-native';
import Svg, { Circle, G, Image as SvgImage, Polygon, Polyline, Text as SvgText } from 'react-native-svg';
import { fitView, pan, planSize, planToScreen, PX_PER_METER, Pt, screenToWorld, View as VT, worldToPlan, zoomAt } from '../lib/floorplan/coords';
import type { Plan } from '../lib/floorplan/plan';
import { STATUS, useTheme } from '../theme';

export type MapPin = { id: string; p: Pt; color: string; label?: string };

type Props = {
  plan: Plan;
  coverage?: Record<string, number>;
  background?: { uri: string } | null;
  pins?: MapPin[];
  path?: Pt[];
  start?: Pt | null;
  height?: number;
  onTapWorld?: (p: Pt) => void;
};

const dist = (a: GestureResponderEvent['nativeEvent']['touches']) => Math.hypot(a[0].pageX - a[1].pageX, a[0].pageY - a[1].pageY);
const mid = (a: GestureResponderEvent['nativeEvent']['touches']): Pt => [(a[0].locationX + a[1].locationX) / 2, (a[0].locationY + a[1].locationY) / 2];

export function FloorplanView({ plan, coverage = {}, background, pins = [], path = [], start, height = 380, onTapWorld }: Props) {
  const t = useTheme();
  const size = useMemo(() => planSize(plan.bounds), [plan.bounds]);
  const [box, setBox] = useState({ w: 360, h: height });
  const [view, setView] = useState<VT>(() => fitView(size, 360, height));
  const viewRef = useRef(view);
  viewRef.current = view;
  const gesture = useRef({ lastX: 0, lastY: 0, pinch: 0, moved: 0, t0: 0 });
  const laidOut = useRef(false);

  const onLayout = (e: LayoutChangeEvent) => {
    const { width, height: h } = e.nativeEvent.layout;
    setBox({ w: width, h });
    if (!laidOut.current) {
      laidOut.current = true;
      setView(fitView(size, width, h));
    }
  };

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (e) => {
          const n = e.nativeEvent;
          gesture.current = { lastX: n.pageX, lastY: n.pageY, pinch: n.touches.length > 1 ? dist(n.touches) : 0, moved: 0, t0: Date.now() };
        },
        onPanResponderMove: (e) => {
          const n = e.nativeEvent;
          const g = gesture.current;
          if (n.touches.length > 1) {
            const d = dist(n.touches);
            if (g.pinch > 0) setView((v) => zoomAt(v, d / g.pinch, mid(n.touches)));
            g.pinch = d;
            g.moved += 100;
          } else {
            const dx = n.pageX - g.lastX;
            const dy = n.pageY - g.lastY;
            g.moved += Math.abs(dx) + Math.abs(dy);
            g.lastX = n.pageX;
            g.lastY = n.pageY;
            g.pinch = 0;
            setView((v) => pan(v, dx, dy));
          }
        },
        onPanResponderRelease: (e) => {
          const g = gesture.current;
          if (g.moved < 12 && Date.now() - g.t0 < 400 && onTapWorld) {
            onTapWorld(screenToWorld([e.nativeEvent.locationX, e.nativeEvent.locationY], viewRef.current, plan.bounds));
          }
        },
      }),
    [onTapWorld, plan.bounds],
  );

  const zoomBtn = (f: number) => setView((v) => zoomAt(v, f, [box.w / 2, box.h / 2]));
  const toPlan = (p: Pt) => worldToPlan(p, plan.bounds);

  return (
    <View style={{ height, borderWidth: 2, borderColor: t.border, borderRadius: 12, overflow: 'hidden', backgroundColor: t.bg }} onLayout={onLayout} {...responder.panHandlers}>
      <Svg width={box.w} height={box.h} pointerEvents="none">
        <G transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
          {background ? <SvgImage href={{ uri: background.uri }} x={0} y={0} width={size.w} height={size.h} preserveAspectRatio="xMidYMid meet" opacity={0.7} /> : null}
          {plan.zones.map((z) => {
            const cov = coverage[z.id] ?? 0;
            const color = cov >= 0.8 ? STATUS.installed : cov > 0 ? STATUS.inProgress : STATUS.notStarted;
            const pts = z.polygon.map(toPlan);
            const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
            const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
            return (
              <G key={z.id}>
                <Polygon points={pts.map((p) => p.join(',')).join(' ')} fill={color} fillOpacity={0.3} stroke={color} strokeWidth={3 / view.scale} />
                <SvgText x={cx} y={cy} fontSize={14 / Math.max(view.scale, 0.3)} fontWeight="bold" fill={t.text} textAnchor="middle">{`${z.name} ${Math.round(cov * 100)}%`}</SvgText>
              </G>
            );
          })}
          {path.length > 1 ? <Polyline points={path.map((p) => toPlan(p).join(',')).join(' ')} fill="none" stroke={t.primary} strokeWidth={3 / view.scale} strokeDasharray={`${8 / view.scale} ${6 / view.scale}`} /> : null}
          {start ? <Circle cx={toPlan(start)[0]} cy={toPlan(start)[1]} r={9 / view.scale} fill={STATUS.installed} stroke={t.border} strokeWidth={2 / view.scale} /> : null}
          {pins.map((pin) => (
            <Circle key={pin.id} cx={toPlan(pin.p)[0]} cy={toPlan(pin.p)[1]} r={8 / view.scale} fill={pin.color} stroke={t.border} strokeWidth={2 / view.scale} />
          ))}
        </G>
      </Svg>
      <View style={{ position: 'absolute', right: 8, top: 8 }}>
        <ZoomBtn label="+" onPress={() => zoomBtn(1.4)} />
        <ZoomBtn label="−" onPress={() => zoomBtn(1 / 1.4)} />
        <ZoomBtn label="⤢" onPress={() => setView(fitView(size, box.w, box.h))} />
      </View>
    </View>
  );
}

import { Pressable, Text } from 'react-native';
function ZoomBtn({ label, onPress }: { label: string; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={{ width: 48, height: 48, marginBottom: 6, borderRadius: 10, borderWidth: 2, borderColor: t.border, backgroundColor: t.card, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: t.text, fontSize: 24, fontWeight: '800' }}>{label}</Text>
    </Pressable>
  );
}
