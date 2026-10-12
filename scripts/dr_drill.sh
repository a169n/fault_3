#!/usr/bin/env bash
# Part E - disaster-recovery drill on a throw-away local PostgreSQL instance.
#
# Measures real RPO / RTO for two backup strategies:
#   A) logical backup  (pg_dump every N minutes)      -> RPO = time since last dump
#   B) base backup + continuous WAL archiving (PITR)   -> RPO = seconds
# Disaster = human error `DROP TABLE registrations` - something replication
# (high availability) would copy to the standby immediately.
#
# Requirements: PostgreSQL 14+ client+server binaries in PATH or PGBIN
#   macOS:  brew install postgresql@16 ; export PGBIN=$(brew --prefix postgresql@16)/bin
# Run as a normal (non-root) user:  bash scripts/dr_drill.sh
# Output: results/dr_drill.log  (all data is synthetic)
set -euo pipefail

PGBIN="${PGBIN:-$(pg_config --bindir 2>/dev/null || echo /usr/lib/postgresql/16/bin)}"
WORK="${WORK:-${TMPDIR:-/tmp}/aitu-dr-drill}"
PORT="${PORT:-55433}"
OUT="${OUT:-$(cd "$(dirname "$0")/.." && pwd)/results/dr_drill.log}"
PSQL=("$PGBIN/psql" -X -q -h 127.0.0.1 -p "$PORT" -U postgres -d registration -v ON_ERROR_STOP=1)

now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
elapsed() { python3 -c "print(f'{$2 - $1:.2f}')"; }
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$OUT"; }
count() { "${PSQL[@]}" -t -A -c "SELECT count(*) FROM registrations" 2>/dev/null || echo "table missing"; }
start_pg() { "$PGBIN/pg_ctl" -D "$1" -o "-p $PORT -k $WORK -c listen_addresses=127.0.0.1" -l "$WORK/pg.log" -w start >/dev/null; }
stop_pg() { "$PGBIN/pg_ctl" -D "$1" -m fast -w stop >/dev/null 2>&1 || true; }
insert() {  # insert N synthetic registrations
  "${PSQL[@]}" -c "INSERT INTO registrations (student_id, course_code)
                   SELECT 'S' || lpad((random()*9999)::int::text, 4, '0'), 'CC-' || (300 + g % 20)
                   FROM generate_series(1, $1) g;"
}

stop_pg "$WORK/data"; stop_pg "$WORK/restored"
rm -rf "$WORK"; mkdir -p "$WORK/wal-archive"; : > "$OUT"
log "PostgreSQL: $("$PGBIN/postgres" --version)   work dir: $WORK"

# ---------------------------------------------------------------- primary with WAL archiving
"$PGBIN/initdb" -D "$WORK/data" -U postgres -A trust >/dev/null
cat >> "$WORK/data/postgresql.conf" <<EOF
wal_level = replica
archive_mode = on
archive_command = 'cp %p $WORK/wal-archive/%f'
archive_timeout = 60          # force a WAL segment switch at least every 60 s -> RPO <= 1 min
EOF
start_pg "$WORK/data"
"$PGBIN/createdb" -h 127.0.0.1 -p "$PORT" -U postgres registration
"${PSQL[@]}" -c "CREATE TABLE registrations (id bigserial PRIMARY KEY, student_id text NOT NULL,
                 course_code text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());"
insert 1000
log "initial data: $(count) synthetic registrations"

# ---------------------------------------------------------------- A) logical backup
t=$(now); "$PGBIN/pg_dump" -h 127.0.0.1 -p "$PORT" -U postgres -d registration -Fc -f "$WORK/registration.dump"
log "A) pg_dump finished in $(elapsed "$t" "$(now)") s, size $(wc -c < "$WORK/registration.dump" | tr -d ' ') bytes"
T_DUMP=$(now)

# ---------------------------------------------------------------- B) base backup for PITR
t=$(now); "$PGBIN/pg_basebackup" -h 127.0.0.1 -p "$PORT" -U postgres -D "$WORK/base" -X fetch -c fast   # fast checkpoint
log "B) pg_basebackup finished in $(elapsed "$t" "$(now)") s"

# ---------------------------------------------------------------- new writes after the backups
sleep 3
insert 250
sleep 1
TARGET=$("${PSQL[@]}" -t -A -c "SELECT now()")      # last known-good moment
T_TARGET=$(now)
log "250 more registrations written AFTER the backups -> total $(count); recovery target = $TARGET"
sleep 1

# ---------------------------------------------------------------- disaster
T_DISASTER=$(now)
"${PSQL[@]}" -c "DROP TABLE registrations;"
log "DISASTER: DROP TABLE registrations (human error). rows now: $(count)"
"${PSQL[@]}" -c "SELECT pg_switch_wal();" >/dev/null   # make sure the last WAL is archived
sleep 2

# ---------------------------------------------------------------- restore A (logical)
t=$(now)
"$PGBIN/pg_restore" -h 127.0.0.1 -p "$PORT" -U postgres -d registration "$WORK/registration.dump"
RTO_A=$(elapsed "$t" "$(now)")
ROWS_A=$(count)
log "A) pg_restore finished in $RTO_A s -> rows $ROWS_A (lost $((1250 - ROWS_A)) rows written in the $(elapsed "$T_DUMP" "$T_DISASTER") s after the dump)"
stop_pg "$WORK/data"

# ---------------------------------------------------------------- restore B (point-in-time recovery)
t=$(now)
cp -R "$WORK/base" "$WORK/restored"
cat >> "$WORK/restored/postgresql.conf" <<EOF
archive_mode = off
restore_command = 'cp $WORK/wal-archive/%f %p'
recovery_target_time = '$TARGET'
recovery_target_action = 'promote'
EOF
touch "$WORK/restored/recovery.signal"
start_pg "$WORK/restored"
until "$PGBIN/pg_isready" -q -h 127.0.0.1 -p "$PORT"; do sleep 0.2; done
until [ "$("${PSQL[@]}" -t -A -c 'SELECT pg_is_in_recovery()')" = "f" ]; do sleep 0.2; done
RTO_B=$(elapsed "$t" "$(now)")
ROWS_B=$(count)
log "B) PITR to '$TARGET' finished in $RTO_B s -> rows $ROWS_B (lost $((1250 - ROWS_B)) rows; data loss window $(elapsed "$T_TARGET" "$T_DISASTER") s before the DROP)"
stop_pg "$WORK/restored"

log "SUMMARY | logical dump: RTO ${RTO_A}s, rows $ROWS_A/1250 | base backup + WAL PITR: RTO ${RTO_B}s, rows $ROWS_B/1250"
