import { useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import clsx from 'clsx';
import { Sidebar } from './Sidebar';
import { RightRail } from './RightRail';
import { CommandPalette } from './CommandPalette';
import { ArtifactPreview } from '../../features/artifacts/ArtifactPreview';
import { useUiStore } from '../../shared/stores/ui';

export function AppShell() {
  const railOpen = useUiStore((state) => state.railOpen);
  const paletteOpen = useUiStore((state) => state.paletteOpen);
  const setPaletteOpen = useUiStore((state) => state.setPaletteOpen);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen(!paletteOpen);
        return;
      }
      if (event.key === 'Escape' && paletteOpen) setPaletteOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [paletteOpen, setPaletteOpen]);

  return (
    <div className={clsx('shell', railOpen && 'shell--rail')}>
      <Sidebar />
      <main className="shell__main">
        <Outlet />
      </main>
      {railOpen && <RightRail />}
      <CommandPalette />
      <ArtifactPreview />
    </div>
  );
}
