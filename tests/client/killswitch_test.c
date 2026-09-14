// The kill switch's decision, on a clock the test owns — see killswitch.h for
// why the logic takes one.

#include "killswitch.h"
#include "procstat.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>

static int failures = 0;

static void check(bool ok, const char *what) {
    if (!ok) {
        printf("not ok - %s\n", what);
        failures++;
    } else {
        printf("ok - %s\n", what);
    }
}

static const ks_input NONE = {false, false, false};
static const ks_input COMBO = {true, true, true};

// Holding the combo from t=0, sampled every `step` ms like the real loop, and
// reporting when it fired.
static uint64_t fires_at(ks_input in, uint64_t hold_ms, uint64_t step, uint64_t until) {
    ks_pad pad;
    ks_init(&pad, hold_ms);
    for (uint64_t now = 0; now <= until; now += step) {
        if (ks_step(&pad, in, now)) return now;
    }
    return 0;
}

static void test_a_held_combo_fires_once_at_three_seconds(void) {
    ks_pad pad;
    ks_init(&pad, 3000);

    int fired = 0;
    for (uint64_t now = 0; now <= 6000; now += 50) {
        if (ks_step(&pad, COMBO, now)) fired++;
    }
    check(fired == 1, "a combo held for six seconds fires exactly once");
    check(fires_at(COMBO, 3000, 50, 6000) == 3000, "and it fires at three seconds, not before");
}

static void test_it_does_not_fire_early(void) {
    check(fires_at(COMBO, 3000, 50, 2950) == 0, "held for 2.95s and let go: nothing happens");
}

static void test_releasing_starts_the_three_seconds_over(void) {
    ks_pad pad;
    ks_init(&pad, 3000);

    // Two and a half seconds of the combo, a moment off it, then the combo
    // again: the second hold is what counts, so nothing fires until 2500 + 100
    // + 3000.
    bool quiet = true;
    for (uint64_t now = 0; now < 2500; now += 50) quiet &= !ks_step(&pad, COMBO, now);
    check(quiet, "quiet through two and a half seconds of holding");
    check(!ks_step(&pad, NONE, 2500), "and quiet on release");

    int fired_at = 0;
    for (uint64_t now = 2600; now <= 6000; now += 50) {
        if (ks_step(&pad, COMBO, now)) { fired_at = (int)now; break; }
    }
    check(fired_at == 5600, "the second hold is timed from its own start");
}

static void test_a_shoulder_short_of_the_combo_never_fires(void) {
    const ks_input partials[] = {
        {true, true, false},   // both shoulders, no Start — the resting grip
        {true, false, true},   // one shoulder and Start
        {false, true, true},
        {false, false, true},  // Start alone, which pauses half these games
    };
    for (size_t i = 0; i < sizeof partials / sizeof *partials; i++) {
        check(fires_at(partials[i], 3000, 50, 30000) == 0, "an incomplete combo never fires, however long");
    }
}

static void test_triggers_count_as_shoulders(void) {
    // The caller decides which physical control filled each side in; what
    // matters here is that a pad reporting its shoulders as axes is not a pad
    // without a kill switch.
    ks_input triggers = {true, true, true};
    check(fires_at(triggers, 3000, 50, 6000) == 3000, "a pad whose L/R are triggers fires the same way");
}

static void test_a_slow_sample_still_fires(void) {
    // The poll interval is ours to choose and a busy machine can stretch it.
    // Three seconds means "three seconds have passed", not "sixty samples".
    check(fires_at(COMBO, 3000, 500, 6000) == 3000, "sampled twice a second, it still fires");
    check(fires_at(COMBO, 3000, 1100, 6000) == 3300, "sampled slower than the hold, it fires late rather than never");
}

static void test_a_second_hold_can_fire_again(void) {
    ks_pad pad;
    ks_init(&pad, 3000);
    for (uint64_t now = 0; now <= 3000; now += 50) ks_step(&pad, COMBO, now);
    ks_step(&pad, NONE, 3050);

    int fired = 0;
    for (uint64_t now = 3100; now <= 7000; now += 50) {
        if (ks_step(&pad, COMBO, now)) fired++;
    }
    check(fired == 1, "a released and re-held combo can fire again");
}

static void test_a_clock_that_goes_backwards_does_not_fire(void) {
    ks_pad pad;
    ks_init(&pad, 3000);
    check(!ks_step(&pad, COMBO, 10000), "the hold begins");
    check(!ks_step(&pad, COMBO, 9000), "a clock that jumped back is not three seconds of holding");
    check(!ks_step(&pad, COMBO, 11000), "and the hold is re-timed from where it landed");
    check(ks_step(&pad, COMBO, 12000), "firing three seconds after that");
}

static void test_held_ms_reports_the_hold(void) {
    ks_pad pad;
    ks_init(&pad, 3000);
    check(ks_held_ms(&pad, 1000) == 0, "nothing held, nothing to report");
    ks_step(&pad, COMBO, 1000);
    check(ks_held_ms(&pad, 2500) == 1500, "a hold reports its own age");
    ks_step(&pad, NONE, 2600);
    check(ks_held_ms(&pad, 2700) == 0, "and stops reporting when it is let go");
}

static void test_a_zero_hold_fires_at_once_rather_than_never(void) {
    // Somebody sets the hold to 0. Defined behaviour rather than a wrap or a
    // divide: it fires on the first sample of the combo, and still only once.
    ks_pad pad;
    ks_init(&pad, 0);
    check(ks_step(&pad, COMBO, 1000), "a zero hold fires on the first sample");
    check(!ks_step(&pad, COMBO, 1050), "and only once, like any other hold");
}

// --- what /proc says -------------------------------------------------------

static void test_the_state_is_read_past_the_process_name(void) {
    // The name is whatever the program was called: it can hold spaces and it
    // can hold a close paren. A parser counting fields from the left reads a
    // process called ") Z 1 1 1" as a zombie and stops guarding a live game.
    check(proc_stat_state("42 (ares) S 1 42 42 0 -1 4194304 100") == 'S', "an ordinary line gives its state");
    check(proc_stat_state("42 (Cemu (Wii U)) R 1 42") == 'R', "a name with parens does not confuse it");
    check(proc_stat_state("42 () Z 1 1 1) S 1 42") == 'S', "a name that spells out a fake state does not either");
    check(proc_stat_state("42 (ares) Z 1 42") == 'Z', "a zombie is seen as a zombie");
    check(proc_stat_state("nonsense") == 0, "a line with no name at all is no answer");
}

static void test_the_start_time_is_the_twenty_second_field(void) {
    // Fields 3..22 of a real line: state, then eighteen numbers, then the
    // start time. Pinning the wrong one would mean a watcher that decides its
    // game has been replaced every time the scheduler moves.
    const char *line =
        "42 (ares) S 1 42 42 0 -1 4194304 1 2 3 4 5 6 7 8 9 10 11 12 987654 "
        "13 14 15 16 17 18";
    check(proc_stat_starttime(line) == 987654, "the start time is read from field 22");
    check(proc_stat_starttime("42 (ares) S 1 42") == 0, "a line that stops short gives nothing");
    check(proc_stat_starttime("") == 0, "and so does an empty one");
}

static void test_a_live_process_is_told_from_one_that_never_existed(void) {
    check(proc_alive(getpid()), "this process is alive");
    check(proc_started(getpid()) > 0, "and has a start time to be pinned by");
    // Above the default pid_max, so it is nobody rather than somebody else.
    check(!proc_alive(4194305), "a pid that cannot exist is not alive");
    check(proc_started(4194305) == 0, "and has no start time to pin");
}

int main(void) {
    test_a_held_combo_fires_once_at_three_seconds();
    test_it_does_not_fire_early();
    test_releasing_starts_the_three_seconds_over();
    test_a_shoulder_short_of_the_combo_never_fires();
    test_triggers_count_as_shoulders();
    test_a_slow_sample_still_fires();
    test_a_second_hold_can_fire_again();
    test_a_clock_that_goes_backwards_does_not_fire();
    test_held_ms_reports_the_hold();
    test_a_zero_hold_fires_at_once_rather_than_never();
    test_the_state_is_read_past_the_process_name();
    test_the_start_time_is_the_twenty_second_field();
    test_a_live_process_is_told_from_one_that_never_existed();

    printf("%s\n", failures ? "FAILED" : "all good");
    return failures ? 1 : 0;
}
