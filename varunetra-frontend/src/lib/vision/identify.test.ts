import { describe, it, expect } from 'vitest';
import { IDENTIFY_SYSTEM, cropOf, identityWords, parseIdentities, pickVehicles } from './identify';
import type { Detection } from './detect';

const det = (label: Detection['label'], w: number, h = w * 0.6): Detection => ({ label, score: 0.9, box: [10, 10, w, h] });

describe('choosing the vehicles to name', () => {
  it('takes the largest vehicles big enough to show anything, never people', () => {
    const d = [det('car', 20), det('person', 200), det('truck', 90), det('car', 40), det('bus', 120)];
    expect(pickVehicles(d)).toEqual([4, 2, 3]);
    expect(pickVehicles(Array.from({ length: 10 }, (_, i) => det('car', 30 + i)))).toHaveLength(6);
  });

  it('crops round a box with a margin, inside the frame', () => {
    expect(cropOf([10, 10, 100, 50], { width: 1000, height: 1000 })).toEqual([0, 4, 122, 62]);
    expect(cropOf([950, 950, 100, 100], { width: 1000, height: 1000 })).toEqual([938, 938, 62, 62]);
  });
});

describe("reading the model's answer", () => {
  it('keeps one checked entry per crop, in order', () => {
    const out = parseIdentities({ vehicles: [
      { n: 2, make: 'Ford', model: 'Transit', body: 'van', colour: 'White', confidence: 'likely' },
      { n: 1, make: 'Toyota', model: 'Corolla', body: 'sedan', colour: null, confidence: 'possible' },
      { n: 9, make: 'Ghost', model: 'X', body: 'car', confidence: 'likely' },
    ] }, 2);
    expect(out).toEqual([
      { n: 1, make: 'Toyota', model: 'Corolla', body: null, colour: null, confidence: 'possible' },
      { n: 2, make: 'Ford', model: 'Transit', body: 'van', colour: 'white', confidence: 'likely' },
    ]);
  });

  it('turns "unknown" into unclear, and never keeps a model without a make', () => {
    const [v] = parseIdentities({ vehicles: [{ n: 1, make: 'Unknown', model: 'Civic', body: 'car', confidence: 'likely' }] }, 1);
    expect(v).toMatchObject({ make: null, model: null, confidence: 'unclear' });
    expect(identityWords(v)).toBe('unclear');
  });

  it('strips markup and caps what it keeps', () => {
    const [v] = parseIdentities({ vehicles: [{ n: 1, make: '<img src=x>Tesla', model: 'Model 3'.repeat(20), confidence: 'likely' }] }, 1);
    expect(v.make).toBe('img src=x Tesla');
    expect(v.model!.length).toBeLessThanOrEqual(40);
    expect(parseIdentities(null, 3)).toEqual([]);
  });

  it('forbids number plates in so many words', () => {
    expect(IDENTIFY_SYSTEM).toMatch(/Never read, transcribe or describe number plates/);
  });
});
