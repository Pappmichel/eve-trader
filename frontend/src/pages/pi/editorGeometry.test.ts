import { describe, expect, it } from 'vitest'
import {
  greatCircleAngle, loadColor, makeProjection, project, round5, tooClose, unproject,
} from './editorGeometry'

describe('editor geometry', () => {
  it('projects and unprojects to the same La/Lo', () => {
    const pts = [{ la: 1.1, lo: 1.5 }, { la: 1.2, lo: 1.62 }, { la: 1.05, lo: 1.58 }]
    const pr = makeProjection(pts, 800, 600)
    for (const p of pts) {
      const back = unproject(project(p, pr), pr)
      expect(back.la).toBeCloseTo(p.la, 9)
      expect(back.lo).toBeCloseTo(p.lo, 9)
    }
  })

  it('keeps the points inside the box', () => {
    const pts = [{ la: 1.1, lo: 1.5 }, { la: 1.3, lo: 1.9 }]
    const pr = makeProjection(pts, 800, 600, 40)
    for (const p of pts) {
      const q = project(p, pr)
      expect(q.x).toBeGreaterThanOrEqual(39.9)
      expect(q.x).toBeLessThanOrEqual(760.1)
      expect(q.y).toBeGreaterThanOrEqual(39.9)
      expect(q.y).toBeLessThanOrEqual(560.1)
    }
  })

  it('great-circle angle: zero for equal points, delta La along a meridian', () => {
    expect(greatCircleAngle({ la: 1, lo: 2 }, { la: 1, lo: 2 })).toBeCloseTo(0, 9)
    expect(greatCircleAngle({ la: 1, lo: 2 }, { la: 1.05, lo: 2 })).toBeCloseTo(0.05, 9)
  })

  it('flags pins closer than 0.012 rad only', () => {
    const pins = [{ la: 1, lo: 1 }, { la: 1.011, lo: 1 }, { la: 1.02, lo: 1 }]
    expect(tooClose({ la: 1, lo: 1 }, pins, 0)).toEqual([1])
    expect(tooClose({ la: 1.0, lo: 1.0 }, pins)).toEqual([0, 1])
  })

  it('colours link load by ratio', () => {
    expect(loadColor(50, 100)).toBe('#51cf66')
    expect(loadColor(80, 100)).toBe('#ff922b')
    expect(loadColor(120, 100)).toBe('#fa5252')
  })

  it('rounds to 5 decimals', () => {
    expect(round5(1.234567891)).toBe(1.23457)
  })
})
