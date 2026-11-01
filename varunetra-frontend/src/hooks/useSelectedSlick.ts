import { useCallback, useEffect, useState } from 'react';
import type { RefObject } from 'react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import type { Slick } from '@/types/slick';

export function useSelectedSlick(mapRef: RefObject<MapLibreMap | null>) {
  const [slick, setSlick] = useState<Slick | null>(null);

  const openSlick = useCallback((nextSlick: Slick) => {
    setSlick(nextSlick);
    const map = mapRef.current;
    if (!map) return;

    const isMobile = window.matchMedia('(max-width: 767px)').matches;
    map.easeTo({
      center: nextSlick.center,
      padding: isMobile
        ? { top: 0, right: 0, bottom: Math.round(window.innerHeight * 0.35), left: 0 }
        : { top: 0, right: 420, bottom: 0, left: 0 },
      duration: 500,
    });
  }, [mapRef]);

  const closeSlick = useCallback(() => {
    setSlick(null);
    mapRef.current?.easeTo({
      padding: { top: 0, right: 0, bottom: 0, left: 0 },
      duration: 500,
    });
  }, [mapRef]);

  useEffect(() => {
    if (!slick) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeSlick();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [slick, closeSlick]);

  return { slick, openSlick, closeSlick };
}
