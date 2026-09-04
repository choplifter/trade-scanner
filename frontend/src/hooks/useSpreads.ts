import { useCallback, useEffect, useRef, useState } from "react";

import { closeSpread, createTrigger, deleteTrigger, getOptionsAccount, getSpreads } from "../api/options";
import { subscribeReplaySession } from "../api/replayMode";
import type {
  CloseSpreadRequest,
  OptionsAccountResponse,
  OrderResponse,
  SpreadGroup,
  TriggerCreateRequest,
  UnderlyingTrigger,
} from "../types/options";

// Same cadence as useTrading: a slow poll, and a hot window after an
// action so the tables do not lag a fill.
const POLL_MS = 4_000;
const HOT_POLL_MS = 1_200;
const HOT_WINDOW_MS = 12_000;
const ERROR_THRESHOLD = 3;

export interface SpreadsState {
  account: OptionsAccountResponse | null;
  spreads: SpreadGroup[];
  triggers: UnderlyingTrigger[];
  loading: boolean;
  error: string | null;
}

export interface SpreadsActions {
  refresh: () => void;
  afterAction: () => void;
  close: (req: CloseSpreadRequest, confirm?: string) => Promise<OrderResponse>;
  armTrigger: (req: TriggerCreateRequest, confirm?: string) => Promise<UnderlyingTrigger>;
  cancelTrigger: (id: string) => Promise<void>;
}

const EMPTY: SpreadsState = { account: null, spreads: [], triggers: [], loading: true, error: null };

/** Open spreads, their triggers and the options account, polled while the
 * Options widget is mounted and refetched on every replay tick (the
 * simulated book's marks move with the replay clock). `enabled` false
 * means no requests at all. */
export function useSpreads(enabled: boolean): SpreadsState & SpreadsActions {
  const [state, setState] = useState<SpreadsState>(EMPTY);
  const cancelledRef = useRef(false);
  const hotUntilRef = useRef(0);
  const failuresRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const [account, spreads] = await Promise.all([getOptionsAccount(), getSpreads()]);
      if (cancelledRef.current) return;
      failuresRef.current = 0;
      setState({
        account,
        spreads: spreads.spreads,
        triggers: spreads.triggers,
        loading: false,
        error: null,
      });
    } catch (err: unknown) {
      if (cancelledRef.current) return;
      failuresRef.current += 1;
      if (failuresRef.current < ERROR_THRESHOLD) return;
      setState((s) => ({ ...s, loading: false, error: err instanceof Error ? err.message : String(err) }));
    }
  }, [enabled]);

  useEffect(() => {
    cancelledRef.current = false;
    if (!enabled) {
      setState({ ...EMPTY, loading: false });
      return;
    }
    setState(EMPTY);
    void refresh();
    let sinceLast = 0;
    const timer = setInterval(() => {
      sinceLast += HOT_POLL_MS;
      const hot = Date.now() < hotUntilRef.current;
      if (hot || sinceLast >= POLL_MS) {
        sinceLast = 0;
        void refresh();
      }
    }, HOT_POLL_MS);
    let debounce: number | null = null;
    const unsubscribe = subscribeReplaySession(() => {
      if (debounce != null) window.clearTimeout(debounce);
      debounce = window.setTimeout(() => void refresh(), 300);
    });
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
      unsubscribe();
      if (debounce != null) window.clearTimeout(debounce);
    };
  }, [enabled, refresh]);

  const afterAction = useCallback(() => {
    hotUntilRef.current = Date.now() + HOT_WINDOW_MS;
    void refresh();
  }, [refresh]);

  return {
    ...state,
    refresh: () => void refresh(),
    afterAction,
    close: async (req, confirm) => {
      const result = await closeSpread(req, confirm);
      afterAction();
      return result;
    },
    armTrigger: async (req, confirm) => {
      const result = await createTrigger(req, confirm);
      afterAction();
      return result.trigger;
    },
    cancelTrigger: async (id) => {
      await deleteTrigger(id);
      afterAction();
    },
  };
}
