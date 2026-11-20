import * as Notifications from 'expo-notifications';
import { Tabs } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import React from 'react';
import { Text, useColorScheme } from 'react-native';
import { AppProvider } from '../lib/store/app';
import { useTheme } from '../theme';

Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: false, shouldSetBadge: false }),
});

const icon = (g: string) => ({ color }: { color: any }) => <Text style={{ fontSize: 22, color }}>{g}</Text>;

function Inner() {
  const t = useTheme();
  return (
    <>
      <StatusBar style={useColorScheme() === 'dark' ? 'light' : 'dark'} />
      <Tabs
        screenOptions={{
          headerStyle: { backgroundColor: t.bg },
          headerTitleStyle: { color: t.text, fontWeight: '800' },
          tabBarStyle: { backgroundColor: t.bg, borderTopColor: t.border, borderTopWidth: 2, height: 72, paddingBottom: 8 },
          tabBarLabelStyle: { fontSize: 14, fontWeight: '800' },
          tabBarActiveTintColor: t.primary,
          tabBarInactiveTintColor: t.sub,
        }}>
        <Tabs.Screen name="index" options={{ title: 'Camera', tabBarIcon: icon('◉') }} />
        <Tabs.Screen name="projects" options={{ title: 'Projects', tabBarIcon: icon('▦') }} />
        <Tabs.Screen name="walk" options={{ title: 'Walk', tabBarIcon: icon('➤') }} />
        <Tabs.Screen name="queue" options={{ title: 'Upload', tabBarIcon: icon('⇧') }} />
      </Tabs>
    </>
  );
}

export default function Layout() {
  return (
    <AppProvider>
      <Inner />
    </AppProvider>
  );
}
