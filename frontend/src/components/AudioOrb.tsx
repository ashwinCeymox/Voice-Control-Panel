import React, { useEffect, useRef, useState } from 'react';

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'loading';

interface AudioOrbProps {
  state: OrbState;
  analyser?: AnalyserNode | null;
  size?: number;
  className?: string;
  onClick?: () => void;
}

interface OrbColorConfig {
  coreStart: string;
  coreEnd: string;
  accent: string;
  glow: string;
}

const STATE_COLORS: Record<OrbState, OrbColorConfig> = {
  idle: {
    coreStart: '#818cf8', // Indigo-400
    coreEnd: '#4f46e5',   // Indigo-600
    accent: '#312e81',    // Indigo-900
    glow: 'rgba(99, 102, 241, 0.25)',
  },
  listening: {
    coreStart: '#38bdf8', // Sky-400
    coreEnd: '#0284c7',   // Sky-600
    accent: '#0369a1',    // Sky-700
    glow: 'rgba(14, 165, 233, 0.45)',
  },
  thinking: {
    coreStart: '#f472b6', // Pink-400
    coreEnd: '#c084fc',   // Purple-400
    accent: '#9333ea',    // Purple-600
    glow: 'rgba(192, 132, 252, 0.35)',
  },
  speaking: {
    coreStart: '#34d399', // Emerald-400
    coreEnd: '#059669',   // Emerald-600
    accent: '#065f46',    // Emerald-800
    glow: 'rgba(16, 185, 129, 0.45)',
  },
  loading: {
    coreStart: '#fbbf24', // Amber-400
    coreEnd: '#d97706',   // Amber-600
    accent: '#78350f',    // Amber-900
    glow: 'rgba(245, 158, 11, 0.35)',
  },
};

export const AudioOrb: React.FC<AudioOrbProps> = ({
  state,
  analyser,
  size = 280,
  className = '',
  onClick,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const phaseRef = useRef<number>(0);
  const smoothedLevelRef = useRef<number>(0);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState<boolean>(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setPrefersReducedMotion(mediaQuery.matches);

    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animId: number;
    const freqData = new Uint8Array(64);

    const render = () => {
      ctx.clearRect(0, 0, size, size);
      ctx.save();

      const centerX = size / 2;
      const centerY = size / 2;
      const baseRadius = size * 0.28;

      // 1. Audio Level Extraction & Envelope Smoothing
      let rawLevel = 0;
      if (analyser && (state === 'listening' || state === 'speaking')) {
        analyser.getByteFrequencyData(freqData);
        let sum = 0;
        const count = Math.min(freqData.length, 32);
        for (let i = 0; i < count; i++) {
          sum += freqData[i];
        }
        rawLevel = sum / (count * 255);
      }

      const attack = 0.35;
      const decay = 0.08;
      if (rawLevel > smoothedLevelRef.current) {
        smoothedLevelRef.current += (rawLevel - smoothedLevelRef.current) * attack;
      } else {
        smoothedLevelRef.current += (rawLevel - smoothedLevelRef.current) * decay;
      }
      const audioScale = smoothedLevelRef.current;

      phaseRef.current += state === 'thinking' ? 0.05 : 0.02;
      const phase = phaseRef.current;
      const colors = STATE_COLORS[state] || STATE_COLORS.idle;

      // 2. Reduced Motion Fallback
      if (prefersReducedMotion) {
        const radGrad = ctx.createRadialGradient(centerX, centerY, 5, centerX, centerY, baseRadius);
        radGrad.addColorStop(0, colors.coreStart);
        radGrad.addColorStop(1, colors.coreEnd);
        ctx.fillStyle = radGrad;
        ctx.beginPath();
        ctx.arc(centerX, centerY, baseRadius, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
        animId = requestAnimationFrame(render);
        return;
      }

      // 3. Ambient Outer Glow
      const glowGrad = ctx.createRadialGradient(
        centerX,
        centerY,
        baseRadius * 0.5,
        centerX,
        centerY,
        baseRadius * (1.6 + audioScale * 0.4)
      );
      glowGrad.addColorStop(0, colors.glow);
      glowGrad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = glowGrad;
      ctx.beginPath();
      ctx.arc(centerX, centerY, baseRadius * (1.6 + audioScale * 0.4), 0, Math.PI * 2);
      ctx.fill();

      // 4. Thinking / Loading Rotating Orbitals
      if (state === 'thinking' || state === 'loading') {
        const orbitRadius = baseRadius * 1.35;
        const orbitCount = state === 'loading' ? 12 : 4;
        for (let i = 0; i < orbitCount; i++) {
          const angle = phase * 1.5 + (i * (Math.PI * 2)) / orbitCount;
          const ox = centerX + Math.cos(angle) * orbitRadius;
          const oy = centerY + Math.sin(angle) * orbitRadius;
          ctx.beginPath();
          ctx.arc(ox, oy, state === 'loading' ? 2.5 : 4, 0, Math.PI * 2);
          ctx.fillStyle = colors.accent;
          ctx.fill();
        }
      }

      // 5. Dynamic Organic Spline Blob
      const numPoints = 12;
      const points: { x: number; y: number }[] = [];
      const breathing = Math.sin(phase) * (baseRadius * 0.04);
      const effectiveBase = baseRadius + breathing + audioScale * baseRadius * 0.45;

      for (let i = 0; i < numPoints; i++) {
        const angle = (i / numPoints) * Math.PI * 2;
        let wave = 0;
        if (state === 'listening' || state === 'speaking') {
          wave =
            Math.sin(angle * 3 + phase * 2) * (audioScale * 18) +
            Math.cos(angle * 2 - phase * 3) * (audioScale * 12);
        } else if (state === 'thinking') {
          wave = Math.sin(angle * 4 + phase * 3) * 6;
        } else {
          wave = Math.sin(angle * 2 + phase) * 3;
        }

        const r = effectiveBase + wave;
        points.push({
          x: centerX + Math.cos(angle) * r,
          y: centerY + Math.sin(angle) * r,
        });
      }

      // Draw Closed Catmull/Bezier Curve through Midpoints
      ctx.beginPath();
      const firstMidX = (points[0].x + points[numPoints - 1].x) / 2;
      const firstMidY = (points[0].y + points[numPoints - 1].y) / 2;
      ctx.moveTo(firstMidX, firstMidY);

      for (let i = 0; i < numPoints; i++) {
        const current = points[i];
        const next = points[(i + 1) % numPoints];
        const midX = (current.x + next.x) / 2;
        const midY = (current.y + next.y) / 2;
        ctx.quadraticCurveTo(current.x, current.y, midX, midY);
      }
      ctx.closePath();

      // Fluid Gradient Fill
      const grad = ctx.createRadialGradient(
        centerX - baseRadius * 0.2,
        centerY - baseRadius * 0.2,
        baseRadius * 0.1,
        centerX,
        centerY,
        effectiveBase
      );
      grad.addColorStop(0, colors.coreStart);
      grad.addColorStop(0.7, colors.coreEnd);
      grad.addColorStop(1, colors.accent);
      ctx.fillStyle = grad;
      ctx.fill();

      // Specular Highlight Arc
      ctx.beginPath();
      ctx.arc(
        centerX - baseRadius * 0.25,
        centerY - baseRadius * 0.25,
        effectiveBase * 0.45,
        Math.PI * 1.1,
        Math.PI * 1.6
      );
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
      ctx.lineWidth = 3;
      ctx.stroke();

      ctx.restore();
      animId = requestAnimationFrame(render);
    };

    animId = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animId);
  }, [state, analyser, size, prefersReducedMotion]);

  return (
    <div
      className={`relative flex items-center justify-center cursor-pointer select-none ${className}`}
      onClick={onClick}
    >
      <canvas
        ref={canvasRef}
        width={size}
        height={size}
        className="transition-transform duration-300 hover:scale-105"
      />
    </div>
  );
};
