import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Props {
  resource: string;
  src: string;
  alt: string;
  className?: string;
}

/**
 * An image the server only serves against a ticket. If the picture fails to load (a ticket that
 * expired, a hiccup), one fresh ticket is requested and the load retried once; then a short fallback.
 */
export function TicketImage({ resource, src, alt, className }: Props) {
  const [ticket, setTicket] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const retries = useRef(0);
  const alive = useRef(true);

  const load = useCallback(() => {
    api
      .ticket(resource)
      .then((t) => alive.current && setTicket(t))
      .catch(() => alive.current && setFailed(true));
  }, [resource]);

  useEffect(() => {
    alive.current = true;
    load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  function onError() {
    if (retries.current < 1) {
      retries.current += 1;
      setTicket(null);
      load();
    } else {
      setFailed(true);
    }
  }

  if (failed) return <p className="note">Image indisponible pour le moment.</p>;
  if (ticket === null) return <p className="note">Chargement…</p>;
  return <img className={className} src={`${src}?ticket=${encodeURIComponent(ticket)}`} alt={alt} onError={onError} />;
}
