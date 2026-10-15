import json, sys
raw = open(sys.argv[1], encoding="ascii", errors="replace").read()
d, _ = json.JSONDecoder().raw_decode(raw[raw.index("{"):])
print("idle avail MB:", d["idle_mem_mb"]["MemAvailable"], "swapfree:", d["idle_mem_mb"]["SwapFree"], "idle temp:", d["idle_temp_c"])
print(f'{"model":13} {"case":28} ok  wall  load  ttft  tok  tok/s  rssMB  minAvl swIn swOut majF cpu%  temp resp')
for r in d["runs"]:
    print(f'{r["model"]:13} {r["case"]:28} {int(r["ok"])}  {r["wall_s"]:5} {r["load_s"]:5} {r["ttft_s_approx"]:5} {r["gen_tokens"]:3} {r["tok_per_s"]:6} {r["peak_ollama_rss_mb"]:6} {r["min_mem_available_mb"]:6} {r["swap_in_pages"]:4} {r["swap_out_pages"]:5} {r["major_faults"]:4} {r["avg_cpu_pct"]}  {r["peak_temp_c"]} {r["response"][:30]!r}')
print("after avail MB:", d["after_mem_mb"]["MemAvailable"])
