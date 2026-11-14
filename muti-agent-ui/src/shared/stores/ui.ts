import { create } from 'zustand';
import type { ArtifactKind } from '../api/types';

export type ThemeMode = 'light' | 'dark';

export interface PreviewTarget {
  id: string;
  title: string;
  kind: ArtifactKind;
  filename?: string;
}

function initialTheme(): ThemeMode {
  const stored = localStorage.getItem('ma.theme');
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

interface UiState {
  theme: ThemeMode;
  railOpen: boolean;
  paletteOpen: boolean;
  preview: PreviewTarget | null;
  setTheme: (theme: ThemeMode) => void;
  toggleTheme: () => void;
  setRailOpen: (open: boolean) => void;
  setPaletteOpen: (open: boolean) => void;
  openPreview: (target: PreviewTarget) => void;
  closePreview: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  theme: initialTheme(),
  railOpen: localStorage.getItem('ma.rail') !== 'closed',
  paletteOpen: false,
  preview: null,

  setTheme: (theme) => {
    localStorage.setItem('ma.theme', theme);
    document.documentElement.dataset.theme = theme;
    set({ theme });
  },

  toggleTheme: () =>
    set((state) => {
      const next: ThemeMode = state.theme === 'dark' ? 'light' : 'dark';
      localStorage.setItem('ma.theme', next);
      document.documentElement.dataset.theme = next;
      return { theme: next };
    }),

  setRailOpen: (open) => {
    localStorage.setItem('ma.rail', open ? 'open' : 'closed');
    set({ railOpen: open });
  },

  setPaletteOpen: (open) => set({ paletteOpen: open }),
  openPreview: (target) => set({ preview: target }),
  closePreview: () => set({ preview: null }),
}));

export function applyInitialTheme(): void {
  document.documentElement.dataset.theme = useUiStore.getState().theme;
}
