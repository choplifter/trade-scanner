import { useSymbolInfo } from "../../hooks/useSymbolInfo";

interface SymbolInfoPanelProps {
  symbol: string | null;
}

export function SymbolInfoPanel({ symbol }: SymbolInfoPanelProps) {
  const { info, loading, error } = useSymbolInfo(symbol);

  if (!symbol) return null;
  if (error) return null; // ChartWidget's own error state already covers this symbol
  if (loading && !info) {
    return <div className="symbol-info-panel widget-empty">Loading company info…</div>;
  }
  if (!info) return null;

  const headerBits = [info.company_name, info.sector, info.industry].filter(Boolean);
  const hasProfile = headerBits.length > 0 || !!info.description;

  return (
    <div className="symbol-info-panel">
      {hasProfile && (
        <div className="symbol-info-header">
          {headerBits.length > 0 && <span className="symbol-info-tags">{headerBits.join(" · ")}</span>}
          {info.description && <p className="symbol-info-description">{info.description}</p>}
          {info.website && (
            <a href={info.website} target="_blank" rel="noreferrer" className="symbol-info-website">
              {info.website}
            </a>
          )}
        </div>
      )}

      {info.news.length > 0 ? (
        <div className="symbol-news-list">
          <h3 className="symbol-news-title">Recent News</h3>
          {info.news.map((item, i) => (
            <div className="symbol-news-item" key={i}>
              {item.url ? (
                <a href={item.url} target="_blank" rel="noreferrer" className="symbol-news-headline">
                  {item.headline}
                </a>
              ) : (
                <span className="symbol-news-headline">{item.headline}</span>
              )}
              <span className="symbol-news-source"> — {item.source}</span>
            </div>
          ))}
        </div>
      ) : (
        !hasProfile && (
          <div className="symbol-info-empty widget-empty">
            No company info or news available right now.
          </div>
        )
      )}
    </div>
  );
}
