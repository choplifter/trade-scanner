import { useScannerFeed } from "../../hooks/useScannerFeed";
import { ScannerTable } from "./ScannerTable";

interface ScannerWidgetProps {
  scanner: string;
  title: string;
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
}

export function ScannerWidget({
  scanner,
  title,
  selectedSymbol,
  onSelectSymbol,
}: ScannerWidgetProps) {
  const { rows, loading } = useScannerFeed(scanner);

  return (
    <div className="widget">
      <div className="widget-header">
        <h2>{title}</h2>
        <span className="widget-count">{rows.length}</span>
      </div>
      <div className="widget-body">
        {loading && rows.length === 0 ? (
          <div className="widget-empty">Loading…</div>
        ) : (
          <ScannerTable
            rows={rows}
            selectedSymbol={selectedSymbol}
            onSelectSymbol={onSelectSymbol}
          />
        )}
      </div>
    </div>
  );
}
