import { AudioMetrics } from "../api";

const ms = (value: number | null) => value === null ? "—" : `${Math.round(value)} мс`;

export function AudioDiagnostics({ metrics }: { metrics: AudioMetrics }) {
  const channels = [
    { name: "Мій голос → співрозмовник", queue: metrics.outgoing, latency: metrics.outgoing_latency },
    { name: "Співрозмовник → я", queue: metrics.incoming, latency: metrics.incoming_latency },
  ];
  const overloaded = channels.some(({ queue }) => queue.input.overloaded || queue.playback.overloaded);

  return (
    <details className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 text-xs text-slate-300">
      <summary className="cursor-pointer font-semibold">
        Затримка та стабільність аудіо{overloaded ? " · Потік відстає" : ""}
      </summary>
      {overloaded && (
        <p role="status" className="text-amber-300 mt-3">
          Накопичується аудіо. Перевірте мережу та вибрані аудіопристрої.
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2 mt-4">
        {channels.map(({ name, queue, latency }) => (
          <div key={name} className="space-y-2">
            <p className="font-semibold text-slate-100">{name}{latency.simulated ? " · демонстрація" : ""}</p>
            <p>До отримання перекладу: p50 {ms(latency.capture_to_audio.p50_ms)} · p95 {ms(latency.capture_to_audio.p95_ms)}</p>
            <p>До відтворення, оцінка: p50 {ms(latency.capture_to_playback_estimate.p50_ms)} · p95 {ms(latency.capture_to_playback_estimate.p95_ms)}</p>
            <p>Черга передавання: {ms(queue.input.queued_ms)} · вік найстарішого блока: {ms(queue.input.oldest_ms)}</p>
            <p>Черга відтворення: {ms(queue.playback.queued_ms)} · jitter: {ms(queue.playback.target_ms)} · нестачі аудіо: {queue.playback.underruns}</p>
          </div>
        ))}
      </div>
      <p className="text-slate-400 mt-4">
        Оцінки від першого голосового блока; враховують черги та повідомлену драйвером затримку.
        Точний час до слухача потребує аудіопетлі. Помилки входу: {metrics.input_errors}; збої виходу: {metrics.output_errors}.
      </p>
    </details>
  );
}
