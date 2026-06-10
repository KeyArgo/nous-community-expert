export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const user = url.searchParams.get("user");
    if (!user) return new Response("missing user", { status: 400 });

    const dataUrl = "https://nous-community-expert-dev.pages.dev/search-data.json";
    let data;
    try {
      const resp = await fetch(dataUrl);
      data = await resp.json();
    } catch {
      return new Response(JSON.stringify({ vibe: "no data" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    const entry = (data.leaderboard || []).find(u => u.name === user);
    if (!entry || !entry.mood_context_chunks) {
      return new Response(JSON.stringify({ vibe: "no data" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    const contextStr = entry.mood_context_chunks
      .map(c => `[${c.start_time?.slice(0, 10) || "?"} in #${c.channel}] ${c.text_preview}`)
      .join("\n");

    const prompt = `You characterize a Discord user's "vibe" based on their recent messages. Be specific, observational, and 1-2 sentences max. Avoid clichés like "passionate" or "engaging". Focus on what they're ACTUALLY working on or talking about.

Recent messages from ${user}:
${contextStr}

Characterize their vibe in 1-2 sentences:`;

    let vibe;
    try {
      const aiResp = await env.AI.run("@cf/meta/llama-3.1-8b-instruct", {
        messages: [
          { role: "system", content: "You are a concise Discord user vibe-characterizer. Be specific and observational." },
          { role: "user", content: prompt },
        ],
        max_tokens: 100,
        temperature: 0.7,
      });
      vibe = aiResp.response || "unknown";
    } catch {
      vibe = "unknown";
    }

    return new Response(JSON.stringify({
      vibe,
      updated: new Date().toISOString(),
    }), {
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=3600",
      },
    });
  },
};
