"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Bell,
  ChevronDown,
  Clock3,
  Database,
  ExternalLink,
  Flame,
  Gauge,
  Landmark,
  Loader2,
  Search,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Users,
  X,
} from "lucide-react";

import {
  ColorType,
  createChart,
  LineSeries,
} from "lightweight-charts";

import { supabase } from "../../lib/supabase";


function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  return `$${Number(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}


function percent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  const number = Number(value);

  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
}


function shortDate(date) {
  if (!date) return "—";

  const parsed = new Date(`${date}T12:00:00`);

  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}


function cleanPolitician(name = "") {
  return name
    .replace(/^Hon\.\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
}


function signalClass(signal = "") {
  const value = signal.toLowerCase();

  if (value.includes("fresh")) return "signal fresh";
  if (value.includes("priced")) return "signal priced";
  if (value.includes("fell")) return "signal fell";
  if (value.includes("late")) return "signal late";

  return "signal moderate";
}


function partyLetter(party) {
  if (!party) return "—";

  const p = party.toLowerCase();

  if (p.startsWith("d")) return "D";
  if (p.startsWith("r")) return "R";
  if (p.startsWith("i")) return "I";

  return party.slice(0, 1).toUpperCase();
}


function buildPoliticianStats(trades) {
  const map = new Map();

  trades.forEach((trade) => {
    const politician = cleanPolitician(trade.politician);

    if (!politician) return;

    if (!map.has(politician)) {
      map.set(politician, {
        politician,
        party: trade.party,
        trades: 0,
        purchases: 0,
        winners: 0,
        roiTotal: 0,
        roiCount: 0,
        lagTotal: 0,
      });
    }

    const row = map.get(politician);

    row.trades += 1;

    if (trade.lag_days !== null) {
      row.lagTotal += Number(trade.lag_days);
    }

    if (
      String(trade.transaction_type).toLowerCase() === "purchase"
    ) {
      row.purchases += 1;

      if (trade.real_return_pct !== null) {
        const roi = Number(trade.real_return_pct);

        row.roiTotal += roi;
        row.roiCount += 1;

        if (roi > 0) {
          row.winners += 1;
        }
      }
    }
  });

  return Array.from(map.values())
    .map((row) => {
      const avgROI =
        row.roiCount > 0
          ? row.roiTotal / row.roiCount
          : null;

      const winRate =
        row.roiCount > 0
          ? (row.winners / row.roiCount) * 100
          : null;

      const avgLag =
        row.trades > 0
          ? row.lagTotal / row.trades
          : null;

      const sampleFactor = Math.min(row.purchases / 10, 1);

      const echoScore =
        avgROI === null || winRate === null
          ? 0
          : Math.max(
              0,
              Math.min(
                100,
                50 +
                  avgROI * 2 +
                  (winRate - 50) * 0.25 +
                  sampleFactor * 10
              )
            );

      return {
        ...row,
        avgROI,
        winRate,
        avgLag,
        echoScore,
      };
    })
    .filter((row) => row.purchases > 0)
    .sort((a, b) => b.echoScore - a.echoScore);
}


function PriceChart({ trade }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !trade) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,

      layout: {
        background: {
          type: ColorType.Solid,
          color: "#090b0f",
        },
        textColor: "#7f8794",
        fontFamily:
          'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      },

      grid: {
        vertLines: {
          color: "rgba(255,255,255,0.035)",
        },
        horzLines: {
          color: "rgba(255,255,255,0.035)",
        },
      },

      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.07)",
      },

      timeScale: {
        borderColor: "rgba(255,255,255,0.07)",
        timeVisible: true,
      },

      crosshair: {
        vertLine: {
          color: "rgba(157,255,87,.30)",
        },
        horzLine: {
          color: "rgba(157,255,87,.30)",
        },
      },

      localization: {
        priceFormatter: (price) => `$${price.toFixed(2)}`,
      },
    });

    const series = chart.addSeries(LineSeries, {
      color: "#9dff57",
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 5,
    });

    const map = new Map();

    if (
      trade.transaction_date &&
      trade.transaction_price !== null
    ) {
      map.set(trade.transaction_date, {
        time: trade.transaction_date,
        value: Number(trade.transaction_price),
      });
    }

    if (
      trade.disclosure_date &&
      trade.disclosure_price !== null
    ) {
      map.set(trade.disclosure_date, {
        time: trade.disclosure_date,
        value: Number(trade.disclosure_price),
      });
    }

    if (trade.current_price !== null) {
      const today = new Date().toISOString().slice(0, 10);

      map.set(today, {
        time: today,
        value: Number(trade.current_price),
      });
    }

    const data = Array.from(map.values()).sort((a, b) =>
      String(a.time).localeCompare(String(b.time))
    );

    if (data.length > 0) {
      series.setData(data);
      chart.timeScale().fitContent();
    }

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({
        width: containerRef.current?.clientWidth || 0,
      });
    });

    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [trade]);

  return <div ref={containerRef} className="trading-chart" />;
}


export default function Home() {
  const [trades, setTrades] = useState([]);
  const [selectedTrade, setSelectedTrade] = useState(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("All");
  const [signalFilter, setSignalFilter] = useState("All");

  const [activeView, setActiveView] = useState("Terminal");


  useEffect(() => {
    loadTrades();
  }, []);


  async function loadTrades() {
    setLoading(true);
    setError("");

    const { data, error } = await supabase
      .from("trades")
      .select("*")
      .order("disclosure_date", {
        ascending: false,
      })
      .order("id", {
        ascending: false,
      })
      .limit(500);

    if (error) {
      console.error(error);

      setError(
        "Capital Echo could not read the Supabase trades table."
      );

      setLoading(false);
      return;
    }

    const rows = data || [];

    setTrades(rows);

    if (rows.length > 0) {
      setSelectedTrade(rows[0]);
    }

    setLoading(false);
  }


  const filteredTrades = useMemo(() => {
    const term = search.toLowerCase().trim();

    return trades.filter((trade) => {
      const searchMatch =
        !term ||
        String(trade.ticker || "")
          .toLowerCase()
          .includes(term) ||
        String(trade.asset_name || "")
          .toLowerCase()
          .includes(term) ||
        String(trade.politician || "")
          .toLowerCase()
          .includes(term);

      const typeMatch =
        typeFilter === "All" ||
        trade.transaction_type === typeFilter;

      const signalMatch =
        signalFilter === "All" ||
        trade.signal_status === signalFilter;

      return searchMatch && typeMatch && signalMatch;
    });
  }, [trades, search, typeFilter, signalFilter]);


  const politicianStats = useMemo(
    () => buildPoliticianStats(trades),
    [trades]
  );


  const purchases = useMemo(
    () =>
      trades.filter(
        (trade) =>
          String(trade.transaction_type).toLowerCase() ===
          "purchase"
      ),
    [trades]
  );


  const freshSignals = useMemo(
    () =>
      trades.filter((trade) =>
        String(trade.signal_status)
          .toLowerCase()
          .includes("fresh")
      ).length,
    [trades]
  );


  const avgLag = useMemo(() => {
    const rows = trades.filter(
      (trade) => trade.lag_days !== null
    );

    if (!rows.length) return 0;

    return (
      rows.reduce(
        (total, trade) =>
          total + Number(trade.lag_days),
        0
      ) / rows.length
    );
  }, [trades]);


  const avgFollowerROI = useMemo(() => {
    const rows = purchases.filter(
      (trade) => trade.real_return_pct !== null
    );

    if (!rows.length) return 0;

    return (
      rows.reduce(
        (total, trade) =>
          total + Number(trade.real_return_pct),
        0
      ) / rows.length
    );
  }, [purchases]);


  const pricedIn = useMemo(
    () =>
      trades.filter((trade) =>
        String(trade.signal_status)
          .toLowerCase()
          .includes("priced")
      ).length,
    [trades]
  );


  const selectedPositive =
    Number(selectedTrade?.real_return_pct || 0) >= 0;


  return (
    <main className="app-shell">

      <aside className="sidebar">

        <div className="brand">
          <div className="brand-mark">
            <TrendingUp size={21} />
          </div>

          <div>
            <div className="brand-name">
              CAPITAL<span>ECHO</span>
            </div>

            <div className="brand-subtitle">
              MARKET INTELLIGENCE
            </div>
          </div>
        </div>


        <div className="sidebar-section-label">
          COMMAND
        </div>

        <nav className="nav-list">

          <button
            className={
              activeView === "Terminal"
                ? "nav-item active"
                : "nav-item"
            }
            onClick={() => setActiveView("Terminal")}
          >
            <BarChart3 size={17} />
            Terminal
          </button>

          <button
            className={
              activeView === "Leaderboard"
                ? "nav-item active"
                : "nav-item"
            }
            onClick={() =>
              setActiveView("Leaderboard")
            }
          >
            <Users size={17} />
            Leaderboard
          </button>

          <button
            className={
              activeView === "Signals"
                ? "nav-item active"
                : "nav-item"
            }
            onClick={() => setActiveView("Signals")}
          >
            <Flame size={17} />
            Signals
          </button>

        </nav>


        <div className="sidebar-section-label secondary">
          DATA
        </div>

        <div className="system-card">
          <div className="system-row">
            <span>
              <Database size={14} />
              Supabase
            </span>

            <div className="live-dot-wrapper">
              <i />
              LIVE
            </div>
          </div>

          <div className="system-row">
            <span>
              <Landmark size={14} />
              House PTR
            </span>

            <strong>{trades.length}</strong>
          </div>

          <div className="system-row">
            <span>
              <Clock3 size={14} />
              Engine
            </span>

            <strong>30M</strong>
          </div>
        </div>


        <div className="sidebar-footer">
          <div className="pulse">
            <span />
          </div>

          <div>
            <strong>Data engine online</strong>
            <small>Capital Echo v0.1</small>
          </div>
        </div>

      </aside>


      <section className="workspace">

        <header className="topbar">

          <div>
            <h1>
              {activeView === "Terminal" &&
                "Intelligence Terminal"}

              {activeView === "Leaderboard" &&
                "Politician Leaderboard"}

              {activeView === "Signals" &&
                "Signal Scanner"}
            </h1>

            <p>
              Congressional disclosure intelligence,
              adjusted for public filing lag.
            </p>
          </div>


          <div className="top-actions">

            <div className="market-status">
              <span />
              DATA LIVE
            </div>

            <button className="icon-button">
              <Bell size={18} />
            </button>

            <button className="avatar-button">
              CE
            </button>

          </div>

        </header>


        {loading && (
          <div className="loading-screen">
            <Loader2 className="spinner" size={27} />
            <span>Loading Capital Echo intelligence...</span>
          </div>
        )}


        {!loading && error && (
          <div className="error-card">
            <ShieldAlert size={20} />

            <div>
              <strong>Database connection blocked</strong>
              <p>{error}</p>
            </div>
          </div>
        )}


        {!loading &&
          !error &&
          activeView === "Terminal" && (
            <>

              <section className="metric-grid">

                <div className="metric-card">
                  <div className="metric-top">
                    <span>TRACKED DISCLOSURES</span>
                    <Database size={17} />
                  </div>

                  <strong>{trades.length}</strong>

                  <small>
                    Congressional transactions indexed
                  </small>
                </div>


                <div className="metric-card">
                  <div className="metric-top">
                    <span>FRESH SIGNALS</span>
                    <Flame size={17} />
                  </div>

                  <strong>{freshSignals}</strong>

                  <small>
                    Filing lag under 14 days
                  </small>
                </div>


                <div className="metric-card">
                  <div className="metric-top">
                    <span>AVG FOLLOWER ROI</span>
                    <Activity size={17} />
                  </div>

                  <strong
                    className={
                      avgFollowerROI >= 0
                        ? "positive"
                        : "negative"
                    }
                  >
                    {percent(avgFollowerROI)}
                  </strong>

                  <small>
                    Purchase performance after disclosure
                  </small>
                </div>


                <div className="metric-card">
                  <div className="metric-top">
                    <span>AVG DISCLOSURE LAG</span>
                    <Clock3 size={17} />
                  </div>

                  <strong>
                    {avgLag.toFixed(1)}
                    <em>D</em>
                  </strong>

                  <small>
                    Transaction → public disclosure
                  </small>
                </div>


                <div className="metric-card danger-card">
                  <div className="metric-top">
                    <span>PRICED IN</span>
                    <ShieldAlert size={17} />
                  </div>

                  <strong>{pricedIn}</strong>

                  <small>
                    Signals where ≥20% moved before filing
                  </small>
                </div>

              </section>


              <section className="terminal-grid">

                <div className="panel chart-panel">

                  <div className="panel-heading">

                    <div className="ticker-heading">

                      <div className="ticker-logo">
                        {selectedTrade?.ticker?.slice(0, 1) ||
                          "C"}
                      </div>

                      <div>
                        <div className="ticker-line">
                          <h2>
                            {selectedTrade?.ticker || "—"}
                          </h2>

                          <span>
                            {selectedTrade?.transaction_type ||
                              "—"}
                          </span>
                        </div>

                        <p>
                          {selectedTrade?.asset_name ||
                            "Select a disclosure"}
                        </p>
                      </div>

                    </div>


                    {selectedTrade && (
                      <div className="price-heading">

                        <strong>
                          {money(
                            selectedTrade.current_price
                          )}
                        </strong>

                        <span
                          className={
                            selectedPositive
                              ? "positive"
                              : "negative"
                          }
                        >
                          {selectedPositive ? (
                            <ArrowUpRight size={15} />
                          ) : (
                            <ArrowDownRight size={15} />
                          )}

                          {percent(
                            selectedTrade.real_return_pct
                          )}
                        </span>

                      </div>
                    )}

                  </div>


                  <div className="chart-toolbar">

                    <div className="time-buttons">
                      <button>TRADE</button>
                      <button>FILED</button>
                      <button className="selected">
                        NOW
                      </button>
                    </div>

                    <div className="chart-label">
                      TRADINGVIEW LIGHTWEIGHT CHARTS
                    </div>

                  </div>


                  <PriceChart trade={selectedTrade} />


                  {selectedTrade && (
                    <div className="chart-events">

                      <div>
                        <span className="event-dot transaction" />
                        <small>TRANSACTION</small>
                        <strong>
                          {shortDate(
                            selectedTrade.transaction_date
                          )}
                        </strong>
                        <b>
                          {money(
                            selectedTrade.transaction_price
                          )}
                        </b>
                      </div>


                      <div>
                        <span className="event-dot disclosure" />
                        <small>DISCLOSURE</small>
                        <strong>
                          {shortDate(
                            selectedTrade.disclosure_date
                          )}
                        </strong>
                        <b>
                          {money(
                            selectedTrade.disclosure_price
                          )}
                        </b>
                      </div>


                      <div>
                        <span className="event-dot current" />
                        <small>CURRENT</small>
                        <strong>Latest quote</strong>
                        <b>
                          {money(
                            selectedTrade.current_price
                          )}
                        </b>
                      </div>

                    </div>
                  )}

                </div>


                <div className="panel intelligence-panel">

                  <div className="panel-title">
                    <div>
                      <Sparkles size={17} />
                      ECHO INTELLIGENCE
                    </div>

                    <Gauge size={17} />
                  </div>


                  {selectedTrade ? (
                    <>

                      <div className="politician-profile">

                        <div className="politician-avatar">
                          {cleanPolitician(
                            selectedTrade.politician
                          )
                            .split(" ")
                            .map((word) => word[0])
                            .slice(0, 2)
                            .join("")}
                        </div>

                        <div>
                          <small>DISCLOSED BY</small>

                          <strong>
                            {cleanPolitician(
                              selectedTrade.politician
                            )}
                          </strong>

                          <span>
                            {partyLetter(
                              selectedTrade.party
                            )}{" "}
                            • U.S. HOUSE
                          </span>
                        </div>

                      </div>


                      <div className="signal-hero">

                        <span
                          className={signalClass(
                            selectedTrade.signal_status
                          )}
                        >
                          {selectedTrade.signal_status ||
                            "Unknown"}
                        </span>

                        <h3>
                          {selectedTrade.transaction_type}{" "}
                          {selectedTrade.ticker}
                        </h3>

                        <p>
                          The public learned about this trade{" "}
                          <strong>
                            {selectedTrade.lag_days} days
                          </strong>{" "}
                          after the reported transaction date.
                        </p>

                      </div>


                      <div className="intel-stats">

                        <div>
                          <span>Filing lag</span>
                          <strong>
                            {selectedTrade.lag_days ?? "—"}d
                          </strong>
                        </div>

                        <div>
                          <span>Missed move</span>
                          <strong
                            className={
                              Number(
                                selectedTrade.missed_move_pct
                              ) > 0
                                ? "warning"
                                : "positive"
                            }
                          >
                            {percent(
                              selectedTrade.missed_move_pct
                            )}
                          </strong>
                        </div>

                        <div>
                          <span>Follower ROI</span>
                          <strong
                            className={
                              Number(
                                selectedTrade.real_return_pct
                              ) >= 0
                                ? "positive"
                                : "negative"
                            }
                          >
                            {percent(
                              selectedTrade.real_return_pct
                            )}
                          </strong>
                        </div>

                        <div>
                          <span>Reported size</span>
                          <strong className="amount-value">
                            {selectedTrade.amount || "—"}
                          </strong>
                        </div>

                      </div>


                      <a
                        className="filing-button"
                        href={selectedTrade.filing_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        OPEN OFFICIAL FILING
                        <ExternalLink size={15} />
                      </a>

                    </>
                  ) : (
                    <div className="empty-state">
                      Select a trade to inspect.
                    </div>
                  )}

                </div>

              </section>


              <section className="panel activity-panel">

                <div className="activity-header">

                  <div>
                    <h2>Congressional Activity</h2>
                    <p>
                      Latest transactions discovered by the
                      Capital Echo data engine.
                    </p>
                  </div>


                  <div className="filter-bar">

                    <div className="search-box">
                      <Search size={15} />

                      <input
                        placeholder="Search ticker or politician..."
                        value={search}
                        onChange={(event) =>
                          setSearch(event.target.value)
                        }
                      />

                      {search && (
                        <button
                          onClick={() => setSearch("")}
                        >
                          <X size={14} />
                        </button>
                      )}
                    </div>


                    <div className="select-wrap">
                      <select
                        value={typeFilter}
                        onChange={(event) =>
                          setTypeFilter(
                            event.target.value
                          )
                        }
                      >
                        <option>All</option>
                        <option>Purchase</option>
                        <option>Sale</option>
                        <option>Exchange</option>
                      </select>

                      <ChevronDown size={14} />
                    </div>

                  </div>

                </div>


                <div className="trade-table-wrapper">

                  <table className="trade-table">

                    <thead>
                      <tr>
                        <th>POLITICIAN</th>
                        <th>ASSET</th>
                        <th>ACTION</th>
                        <th>TRADE DATE</th>
                        <th>DISCLOSED</th>
                        <th>LAG</th>
                        <th>MISS MOVE</th>
                        <th>FOLLOWER ROI</th>
                        <th>SIGNAL</th>
                      </tr>
                    </thead>


                    <tbody>

                      {filteredTrades
                        .slice(0, 100)
                        .map((trade) => (

                          <tr
                            key={trade.id}
                            className={
                              selectedTrade?.id === trade.id
                                ? "selected-row"
                                : ""
                            }
                            onClick={() =>
                              setSelectedTrade(trade)
                            }
                          >

                            <td>
                              <div className="person-cell">
                                <div className="mini-avatar">
                                  {cleanPolitician(
                                    trade.politician
                                  )
                                    .split(" ")
                                    .map((word) => word[0])
                                    .slice(0, 2)
                                    .join("")}
                                </div>

                                <div>
                                  <strong>
                                    {cleanPolitician(
                                      trade.politician
                                    )}
                                  </strong>

                                  <small>
                                    HOUSE •{" "}
                                    {partyLetter(
                                      trade.party
                                    )}
                                  </small>
                                </div>
                              </div>
                            </td>


                            <td>
                              <div className="asset-cell">
                                <strong>
                                  {trade.ticker}
                                </strong>
                                <small>
                                  {trade.asset_name}
                                </small>
                              </div>
                            </td>


                            <td>
                              <span
                                className={
                                  trade.transaction_type ===
                                  "Purchase"
                                    ? "trade-type purchase"
                                    : "trade-type sale"
                                }
                              >
                                {trade.transaction_type}
                              </span>
                            </td>


                            <td>
                              {shortDate(
                                trade.transaction_date
                              )}
                            </td>


                            <td>
                              {shortDate(
                                trade.disclosure_date
                              )}
                            </td>


                            <td>
                              <strong>
                                {trade.lag_days ?? "—"}d
                              </strong>
                            </td>


                            <td
                              className={
                                Number(
                                  trade.missed_move_pct
                                ) >= 20
                                  ? "warning"
                                  : ""
                              }
                            >
                              {percent(
                                trade.missed_move_pct
                              )}
                            </td>


                            <td
                              className={
                                Number(
                                  trade.real_return_pct
                                ) >= 0
                                  ? "positive"
                                  : "negative"
                              }
                            >
                              {percent(
                                trade.real_return_pct
                              )}
                            </td>


                            <td>
                              <span
                                className={signalClass(
                                  trade.signal_status
                                )}
                              >
                                {trade.signal_status}
                              </span>
                            </td>

                          </tr>
                        ))}

                    </tbody>

                  </table>

                </div>

              </section>

            </>
          )}


        {!loading &&
          !error &&
          activeView === "Leaderboard" && (
            <section className="panel leaderboard-panel">

              <div className="leaderboard-heading">
                <div>
                  <h2>Capital Echo Leaderboard</h2>
                  <p>
                    Ranking based on publicly actionable
                    purchase performance, not pre-disclosure
                    returns.
                  </p>
                </div>

                <div className="echo-badge">
                  <Sparkles size={15} />
                  ECHO SCORE
                </div>
              </div>


              <div className="leaderboard-list">

                {politicianStats
                  .slice(0, 20)
                  .map((person, index) => (

                    <div
                      className="leaderboard-row"
                      key={person.politician}
                    >

                      <div className="rank">
                        #{index + 1}
                      </div>

                      <div className="leaderboard-person">
                        <div className="politician-avatar small">
                          {person.politician
                            .split(" ")
                            .map((part) => part[0])
                            .slice(0, 2)
                            .join("")}
                        </div>

                        <div>
                          <strong>
                            {person.politician}
                          </strong>

                          <span>
                            {person.purchases} purchases
                            tracked
                          </span>
                        </div>
                      </div>


                      <div className="leader-stat">
                        <span>FOLLOWER ROI</span>
                        <strong
                          className={
                            person.avgROI >= 0
                              ? "positive"
                              : "negative"
                          }
                        >
                          {percent(person.avgROI)}
                        </strong>
                      </div>


                      <div className="leader-stat">
                        <span>WIN RATE</span>
                        <strong>
                          {person.winRate?.toFixed(1)}%
                        </strong>
                      </div>


                      <div className="leader-stat">
                        <span>AVG LAG</span>
                        <strong>
                          {person.avgLag?.toFixed(1)}d
                        </strong>
                      </div>


                      <div className="echo-score">
                        <strong>
                          {person.echoScore.toFixed(0)}
                        </strong>
                        <span>/100</span>
                      </div>

                    </div>

                  ))}

              </div>

            </section>
          )}


        {!loading &&
          !error &&
          activeView === "Signals" && (
            <section className="signals-layout">

              <div className="panel signal-column">
                <div className="panel-title">
                  <div>
                    <Flame size={17} />
                    FRESH SIGNALS
                  </div>
                </div>

                {trades
                  .filter((trade) =>
                    String(trade.signal_status).includes(
                      "Fresh"
                    )
                  )
                  .slice(0, 30)
                  .map((trade) => (
                    <button
                      key={trade.id}
                      className="signal-row"
                      onClick={() => {
                        setSelectedTrade(trade);
                        setActiveView("Terminal");
                      }}
                    >
                      <div>
                        <strong>{trade.ticker}</strong>
                        <span>
                          {cleanPolitician(
                            trade.politician
                          )}
                        </span>
                      </div>

                      <div>
                        <b>
                          {trade.lag_days}d lag
                        </b>

                        <small
                          className={
                            Number(
                              trade.real_return_pct
                            ) >= 0
                              ? "positive"
                              : "negative"
                          }
                        >
                          {percent(
                            trade.real_return_pct
                          )}
                        </small>
                      </div>
                    </button>
                  ))}
              </div>


              <div className="panel signal-column">
                <div className="panel-title">
                  <div>
                    <ShieldAlert size={17} />
                    PRICED-IN WARNINGS
                  </div>
                </div>

                {trades
                  .filter((trade) =>
                    String(trade.signal_status).includes(
                      "Priced"
                    )
                  )
                  .slice(0, 30)
                  .map((trade) => (
                    <button
                      key={trade.id}
                      className="signal-row"
                      onClick={() => {
                        setSelectedTrade(trade);
                        setActiveView("Terminal");
                      }}
                    >
                      <div>
                        <strong>{trade.ticker}</strong>

                        <span>
                          {cleanPolitician(
                            trade.politician
                          )}
                        </span>
                      </div>

                      <div>
                        <b className="warning">
                          {percent(
                            trade.missed_move_pct
                          )}
                        </b>

                        <small>
                          moved before filing
                        </small>
                      </div>
                    </button>
                  ))}
              </div>

            </section>
          )}

      </section>

    </main>
  );
}
