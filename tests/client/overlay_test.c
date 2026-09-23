// The overlay's decisions, on a clock the test owns: who is joining, where the
// bar is, what a frame draws, what padmap's lines say. No SDL, no socket to a
// daemon -- a socketpair stands in where a socket is the point.

#define _POSIX_C_SOURCE 200809L

#include <fcntl.h>
#include <math.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "bar.h"
#include "events.h"
#include "frame.h"
#include "padlink.h"
#include "pairing.h"
#include "scene.h"
#include "shapes.h"

static int failures = 0;

static void check(bool ok, const char *what) {
    printf("%s - %s\n", ok ? "ok" : "not ok", what);
    if (!ok) failures++;
}

static bool near(double a, double b, double tolerance) {
    return fabs(a - b) <= tolerance;
}

// --- pairing ------------------------------------------------------------------

static size_t holds_at(gs_pairing *pairing, double now, gs_hold *out) {
    return gs_pairing_now(pairing, now, out, GS_HOLDS_MAX);
}

static void test_a_named_hold_survives_the_daemon_pausing_to_rescan(void) {
    // The ~58 ms rescan gap behind GS_STALE_NAMED; see pairing.h.
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", "Pad", 0.30, 1, 10.0);
    bool kept = true;
    for (int frame = 1; frame <= 7; frame++) kept = kept && holds_at(&pairing, 10.0 + frame * 0.016, out) == 1;
    check(kept, "a named hold is drawn through a 120 ms pause");
    holds_at(&pairing, 10.12, out);
    check(near(out[0].fraction, 0.30 + 0.12 / 1.5, 1e-6), "and carried forward at the hold's own rate");
}

static void test_a_named_release_is_immediate(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.5, 1, 10.0);
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.0, 0, 10.02);
    check(holds_at(&pairing, 10.02, out) == 0, "frac 0 for a named pad ends its hold at once");
}

static void test_a_daemon_that_dies_mid_hold_empties_the_bar_eventually(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.5, 1, 10.0);
    check(holds_at(&pairing, 10.0 + GS_STALE_NAMED - 0.01, out) == 1, "still drawn just inside the safety net");
    check(holds_at(&pairing, 10.0 + GS_STALE_NAMED + 0.01, out) == 0, "and gone just past it");
}

static void test_a_nameless_reading_still_ends_with_silence(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, NULL, NULL, 0.5, 0, 10.0);
    check(holds_at(&pairing, 10.0 + GS_STALE_ANONYMOUS + 0.01, out) == 0,
          "an old daemon's hold ends after three frames of silence");
}

static void test_the_first_press_is_drawn_first(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event12", NULL, 0.2, 2, 10.00);
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.1, 1, 10.30);
    size_t count = holds_at(&pairing, 10.31, out);
    check(count == 2 && strcmp(out[0].key, "/dev/input/event12") == 0, "two holds, in the order they began");
}

static void test_a_fill_that_goes_backwards_is_a_new_press_at_the_back(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.6, 1, 10.00);
    gs_pairing_progress(&pairing, "/dev/input/event12", NULL, 0.3, 2, 10.10);
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.05, 2, 10.20);
    size_t count = holds_at(&pairing, 10.21, out);
    check(count == 2 && strcmp(out[1].key, "/dev/input/event9") == 0, "let go and pressed again goes behind");
}

static void test_the_sweep_never_steps_backwards(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.30, 1, 10.0);
    holds_at(&pairing, 10.10, out);
    double ahead = out[0].fraction;
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.36, 1, 10.10);
    holds_at(&pairing, 10.10, out);
    check(out[0].fraction >= ahead, "a reading landing behind the drawing does not pull it back");
}

static void test_a_claim_clears_the_holds_and_shows_the_seat_for_a_moment(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    int players[GS_JOINED_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.99, 2, 10.0);
    gs_pairing_claim(&pairing, "/dev/input/event9", NULL, 2, 10.01);
    check(holds_at(&pairing, 10.02, out) == 0, "the fill that took the seat is over");
    size_t joined = gs_pairing_joined(&pairing, 10.02, players, GS_JOINED_MAX);
    check(joined == 1 && players[0] == 2, "and player two shows as joined");
    check(gs_pairing_busy(&pairing, 10.02 + GS_JOINED_SHOWN - 0.01), "for a moment");
    check(!gs_pairing_busy(&pairing, 10.02 + GS_JOINED_SHOWN + 0.01), "and then nothing is left to show");
}

static void test_a_claim_leaves_the_other_holds_filling(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.99, 1, 10.0);
    gs_pairing_progress(&pairing, "/dev/input/event12", NULL, 0.40, 2, 10.0);
    gs_pairing_claim(&pairing, "/dev/input/event9", "Pad", 1, 10.01);
    size_t count = holds_at(&pairing, 10.02, out);
    check(count == 1 && strcmp(out[0].key, "/dev/input/event12") == 0 && out[0].fraction >= 0.40f,
          "somebody else's claim does not empty a second person's ring");
}

static void test_a_state_drops_only_the_hold_that_became_a_seat(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.5, 1, 10.0);
    gs_pairing_progress(&pairing, "/dev/input/event12", NULL, 0.2, 2, 10.0);
    // The second pad's reading still says seat one: stale by a tick after
    // somebody else's claim. Identity settles it, not the number.
    gs_pairing_seated(&pairing, "/dev/input/event3", "Other", 1);
    check(holds_at(&pairing, 10.01, out) == 2, "a stale seat number drops nobody");
    gs_pairing_seated(&pairing, "/dev/input/event9", "Pad", 1);
    size_t count = holds_at(&pairing, 10.01, out);
    check(count == 1 && strcmp(out[0].key, "/dev/input/event12") == 0, "the pad now seated is dropped by its node");
}

static void test_nothing_to_show_is_not_busy(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    check(!gs_pairing_busy(&pairing, 10.0), "an empty room keeps the bar up");
}

// --- the bar -----------------------------------------------------------------

static void test_the_bar_slides_down_and_back(void) {
    gs_bar bar;
    gs_bar_init(&bar, 0.25);
    check(gs_bar_position(&bar, 1.0) == 0.0 && gs_bar_gone(&bar, 1.0), "it starts out of sight");
    gs_bar_want(&bar, true, 1.0);
    check(near(gs_bar_position(&bar, 1.125), 0.5, 1e-6), "half way down at half the slide");
    check(gs_bar_position(&bar, 1.25) == 1.0 && !gs_bar_moving(&bar, 1.25), "all the way down at the end");
    gs_bar_want(&bar, false, 2.0);
    check(gs_bar_gone(&bar, 2.25), "and gone a slide after it is sent up");
}

static void test_turning_round_mid_slide_does_not_jump(void) {
    gs_bar bar;
    gs_bar_init(&bar, 0.25);
    gs_bar_want(&bar, true, 1.0);
    double at = gs_bar_position(&bar, 1.05);
    gs_bar_want(&bar, false, 1.05);
    check(near(gs_bar_position(&bar, 1.05), at, 1e-9), "sent back up, it starts from where it was");
    // It had come down `at` of the way, so going back takes `at` of a slide.
    check(gs_bar_gone(&bar, 1.05 + 0.25 * at + 1e-6), "and the way back costs only the distance it came");
}

// --- shapes ------------------------------------------------------------------

#define ROOM GS_MESH_VERTICES
static gs_vertex vertices[ROOM];
static int indices[GS_MESH_INDICES];

static gs_mesh fresh(size_t vertex_room, size_t index_room) {
    return (gs_mesh){vertices, indices, 0, 0, vertex_room, index_room};
}

static void test_an_arc_starts_at_twelve_and_turns_clockwise(void) {
    gs_mesh mesh = fresh(ROOM, GS_MESH_INDICES);
    gs_colour white = {1, 1, 1, 1};
    gs_mesh_arc(&mesh, 100, 100, 50, 10, 0.0f, 0.25f, white, 1.0f);
    // Four vertices per step; the second of the first four is the inner edge
    // at twelve o'clock, the second of the last four at three.
    gs_vertex first = mesh.vertices[1], last = mesh.vertices[mesh.vertex_count - 3];
    check(near(first.x, 100, 0.01) && first.y < 100, "it begins straight up");
    check(last.x > 100 && near(last.y, 100, 0.01), "a quarter turn ends at three o'clock, clockwise");
}

static void test_an_arc_has_a_soft_edge(void) {
    gs_mesh mesh = fresh(ROOM, GS_MESH_INDICES);
    gs_colour white = {1, 1, 1, 1};
    gs_mesh_arc(&mesh, 100, 100, 50, 10, 0.0f, 1.0f, white, 1.0f);
    check(mesh.vertices[0].colour.a == 0.0f && mesh.vertices[3].colour.a == 0.0f, "its outermost vertices fade out");
    check(mesh.vertices[1].colour.a == 1.0f && mesh.vertices[2].colour.a == 1.0f, "and its body is solid");
}

static void test_nothing_is_drawn_for_nothing(void) {
    gs_mesh mesh = fresh(ROOM, GS_MESH_INDICES);
    gs_colour white = {1, 1, 1, 1};
    gs_mesh_arc(&mesh, 100, 100, 50, 10, 0.0f, 0.0f, white, 1.0f);
    check(mesh.vertex_count == 0, "an empty arc is no vertices");
}

static void test_a_full_mesh_draws_less_rather_than_overflowing(void) {
    gs_mesh mesh = fresh(16, 16);
    gs_colour white = {1, 1, 1, 1};
    gs_mesh_arc(&mesh, 100, 100, 50, 10, 0.0f, 1.0f, white, 1.0f);
    gs_mesh_disc(&mesh, 100, 100, 50, white, 1.0f);
    check(mesh.vertex_count <= 16 && mesh.index_count <= 16, "nothing written past the room given");
}

// --- the scene ---------------------------------------------------------------

static float lowest_y(const gs_mesh *mesh) {
    float y = 1e9f;
    for (size_t i = 0; i < mesh->vertex_count; i++) y = fminf(y, mesh->vertices[i].y);
    return y;
}

static void test_a_bar_out_of_sight_draws_nothing(void) {
    gs_mesh mesh = fresh(ROOM, GS_MESH_INDICES);
    gs_scene scene = {.width = 1280, .bar_height = gs_bar_height(800), .position = 0.0, .exit_progress = 0.5};
    gs_scene_build(&scene, &mesh);
    check(mesh.vertex_count == 0, "position 0 is an empty frame");
}

static void test_the_bar_comes_down_from_the_top_edge(void) {
    gs_mesh mesh = fresh(ROOM, GS_MESH_INDICES);
    gs_scene scene = {.width = 1280, .bar_height = gs_bar_height(800), .position = 1.0, .exit_progress = 0.5};
    gs_scene_build(&scene, &mesh);
    check(near(lowest_y(&mesh), 0.0, 0.01), "all the way down, it sits on the top edge");
    scene.position = 0.5;
    gs_scene_build(&scene, &mesh);
    check(near(lowest_y(&mesh), -gs_bar_height(800) / 2.0, 0.01), "half way, half of it is above the screen");
}

static void test_the_exit_ring_outranks_joining(void) {
    float fraction = 0.5f;
    int32_t player = 2;
    gs_mesh with_exit = fresh(ROOM, GS_MESH_INDICES);
    gs_scene scene = {.width = 1280,
                      .bar_height = gs_bar_height(800),
                      .position = 1.0,
                      .fractions = &fraction,
                      .players = &player,
                      .hold_count = 1,
                      .exit_progress = 0.5};
    gs_scene_build(&scene, &with_exit);
    size_t exit_vertices = with_exit.vertex_count;
    // Every vertex sits within the ring's reach of the centre: nothing drawn
    // for the pad that is joining.
    float reach = gs_bar_height(800) * 0.5f;
    bool centred = true;
    for (size_t i = 8; i < exit_vertices; i++) {
        centred = centred && fabsf(with_exit.vertices[i].x - 640.0f) <= reach;
    }
    check(centred, "while the exit is held, only its ring is drawn");
}

static void test_items_are_centred_and_evenly_spaced(void) {
    float bar = gs_bar_height(800);
    check(near(gs_item_x(1280, bar, 0, 1), 640.0, 1e-3), "one item sits in the middle");
    float left = gs_item_x(1280, bar, 0, 2), right = gs_item_x(1280, bar, 1, 2);
    check(near(640.0 - left, right - 640.0, 1e-3), "two sit either side of it, the same distance away");
}

static void test_seats_are_the_picker_s_colours(void) {
    gs_colour one = gs_player_colour(1), five = gs_player_colour(5);
    check(near(one.r, 96 / 255.0, 1e-6) && near(one.g, 176 / 255.0, 1e-6), "player one is the picker's blue");
    check(five.r == one.r && five.g == one.g && five.b == one.b, "a fifth wraps round to the first colour");
}

// --- padmap's lines ----------------------------------------------------------

static void test_a_progress_line_is_read(void) {
    gs_event event;
    const char *line =
        "{\"event\":\"progress\",\"frac\":0.42,\"node\":\"/dev/input/event9\",\"name\":\"Pad\",\"player\":2}";
    check(gs_event_parse(line, strlen(line), &event) && event.type == GS_EVENT_PROGRESS, "progress parses");
    check(near(event.frac, 0.42, 1e-9) && event.player == 2 && strcmp(event.node, "/dev/input/event9") == 0,
          "with its fraction, seat and pad");
}

static void test_a_state_names_who_is_seated(void) {
    gs_event event;
    const char *line = "{\"event\":\"state\",\"players\":[{\"player\":1,\"node\":\"/dev/input/event3\"},"
                       "{\"player\":2,\"name\":\"Pad\"}],\"slots\":4}";
    check(gs_event_parse(line, strlen(line), &event) && event.type == GS_EVENT_STATE && event.seated_count == 2,
          "a state lists both seats");
}

static void test_nonsense_is_refused_and_strangers_ignored(void) {
    gs_event event;
    check(!gs_event_parse("{not json", 9, &event), "a broken line is refused");
    check(!gs_event_parse("[1,2]", 5, &event), "so is a line that is not an object");
    const char *other = "{\"event\":\"sdl_mapping\",\"lines\":[]}";
    check(gs_event_parse(other, strlen(other), &event) && event.type == GS_EVENT_OTHER, "an event it has no use for");
    const char *wild = "{\"event\":\"claim\",\"player\":1e9}";
    check(gs_event_parse(wild, strlen(wild), &event) && event.player == 0, "a seat number out of range is not a seat");
}

static void test_lines_split_across_reads_are_joined(void) {
    gs_link link;
    gs_link_init(&link, "/nonexistent");
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    const char *first = "{\"event\":\"progress\",\"frac\":0.2,\"node\":\"/dev/in";
    const char *rest = "put/event9\",\"player\":1}\n";
    check(gs_link_feed(&link, first, strlen(first), &pairing, 10.0) == 0, "half a line is kept, not applied");
    check(gs_link_feed(&link, rest, strlen(rest), &pairing, 10.0) == 1, "and applied once the rest arrives");
    gs_hold out[GS_HOLDS_MAX];
    check(holds_at(&pairing, 10.0, out) == 1 && strcmp(out[0].key, "/dev/input/event9") == 0,
          "as the pad it names");
}

static void test_a_line_too_long_is_skipped_whole(void) {
    static char huge[GS_LINK_BUFFER + 100];
    memset(huge, 'x', sizeof huge);
    gs_link link;
    gs_link_init(&link, "/nonexistent");
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_link_feed(&link, huge, sizeof huge, &pairing, 10.0);
    const char *after = "\n{\"event\":\"progress\",\"frac\":0.3,\"node\":\"n\"}\n";
    check(gs_link_feed(&link, after, strlen(after), &pairing, 10.0) == 1, "the line after it still parses");
}

static void test_a_socket_is_read_and_its_closing_noticed(void) {
    int ends[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, ends) != 0) {
        check(false, "socketpair");
        return;
    }
    gs_link link;
    gs_link_init(&link, "/nonexistent");
    link.fd = ends[0];
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    const char *line = "{\"event\":\"claim\",\"player\":3}\n";
    if (write(ends[1], line, strlen(line)) < 0) check(false, "write");
    // Blocking socket in the test: one read gets the line, the close ends it.
    close(ends[1]);
    check(gs_link_pump(&link, &pairing, 10.0) == 1, "a claim comes in over the socket");
    check(gs_link_fd(&link) == -1, "and the other end closing drops the connection");
}

// --- the painter's frames -------------------------------------------------------

static void test_a_frame_carries_what_the_bar_draws(void) {
    gs_hold holds[2] = {{.fraction = 0.25, .player = 2, .used = true}, {.fraction = 0.5, .player = 3, .used = true}};
    int joined[1] = {1};
    gs_frame frame;
    gs_frame_pack(&frame, 0.75, 0.0, 12.5, holds, 2, joined, 1);
    check(gs_frame_valid(&frame), "a packed frame is one the painter accepts");
    check(frame.hold_count == 2 && frame.hold_player[1] == 3 && near(frame.hold_fraction[0], 0.25, 1e-6),
          "with its holds, in order");
    check(frame.joined_count == 1 && frame.joined[0] == 1 && near(frame.position, 0.75, 1e-6), "and its bar");
}

static void test_a_frame_fits_a_pipe_write_whole(void) {
    // Written in one go down a pipe and read back whole: that holds only
    // below PIPE_BUF, which POSIX guarantees is at least 512.
    check(sizeof(gs_frame) <= 512, "a frame is smaller than the smallest PIPE_BUF");
}

static void test_a_frame_that_is_not_one_is_refused(void) {
    gs_frame frame;
    gs_frame_pack(&frame, 1.0, 0.0, 0.0, NULL, 0, NULL, 0);
    frame.magic = 0;
    check(!gs_frame_valid(&frame), "the wrong magic is refused");
    gs_frame_pack(&frame, 1.0, 0.0, 0.0, NULL, 0, NULL, 0);
    frame.hold_count = GS_HOLDS_MAX + 1;
    check(!gs_frame_valid(&frame), "and so is a count past the arrays");
}

static void test_packing_more_than_fits_is_clamped(void) {
    gs_hold holds[GS_HOLDS_MAX + 3];
    memset(holds, 0, sizeof holds);
    int joined[GS_JOINED_MAX + 2] = {0};
    gs_frame frame;
    gs_frame_pack(&frame, 1.0, 0.0, 0.0, holds, GS_HOLDS_MAX + 3, joined, GS_JOINED_MAX + 2);
    check(frame.hold_count == GS_HOLDS_MAX && frame.joined_count == GS_JOINED_MAX && gs_frame_valid(&frame),
          "too many holds or seats are cut to what a frame holds");
}

// --- the link, against a peer that will not stop -----------------------------------

static void test_no_runtime_directory_is_no_link(void) {
    char *saved = getenv("XDG_RUNTIME_DIR") ? strdup(getenv("XDG_RUNTIME_DIR")) : NULL;
    unsetenv("XDG_RUNTIME_DIR");
    gs_link link;
    gs_link_init(&link, NULL);
    gs_link_tick(&link, 10.0);
    check(link.path[0] == '\0' && gs_link_fd(&link) == -1, "no XDG_RUNTIME_DIR: nothing in /tmp is trusted");
    if (saved) {
        setenv("XDG_RUNTIME_DIR", saved, 1);
        free(saved);
    }
}

// --- capacity: the room this screen actually has ----------------------------

static void test_a_ninth_hold_evicts_the_oldest(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    char key[32];
    for (int i = 0; i < GS_HOLDS_MAX; i++) {
        snprintf(key, sizeof key, "/dev/input/event%d", i);
        gs_pairing_progress(&pairing, key, NULL, 0.1, i + 1, 10.0 + i * 0.01);
    }
    check(holds_at(&pairing, 10.0 + GS_HOLDS_MAX * 0.01, out) == GS_HOLDS_MAX, "eight holds, eight slots");
    gs_pairing_progress(&pairing, "/dev/input/eventNEW", NULL, 0.1, 9, 10.0 + GS_HOLDS_MAX * 0.01);
    size_t count = holds_at(&pairing, 10.0 + GS_HOLDS_MAX * 0.01, out);
    bool oldest_gone = true, newest_present = false;
    for (size_t i = 0; i < count; i++) {
        if (strcmp(out[i].key, "/dev/input/event0") == 0) oldest_gone = false;
        if (strcmp(out[i].key, "/dev/input/eventNEW") == 0) newest_present = true;
    }
    check(count == GS_HOLDS_MAX && oldest_gone && newest_present, "a ninth press pushes out the oldest, not itself");
}

static void test_a_fifth_joined_seat_evicts_the_oldest(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    int players[GS_JOINED_MAX];
    for (int p = 1; p <= GS_JOINED_MAX; p++) gs_pairing_claim(&pairing, NULL, NULL, p, 10.0 + p * 0.1);
    gs_pairing_claim(&pairing, NULL, NULL, 9, 10.0 + GS_JOINED_MAX * 0.1 + 0.1);
    size_t count = gs_pairing_joined(&pairing, 10.0 + GS_JOINED_MAX * 0.1 + 0.1, players, GS_JOINED_MAX);
    bool has_nine = false, has_one = false;
    for (size_t i = 0; i < count; i++) {
        if (players[i] == 9) has_nine = true;
        if (players[i] == 1) has_one = true;
    }
    check(count == GS_JOINED_MAX && has_nine && !has_one, "a fifth seat pushes out the oldest");
}

static void test_a_repeated_claim_refreshes_rather_than_duplicates(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    int players[GS_JOINED_MAX];
    gs_pairing_claim(&pairing, NULL, NULL, 3, 10.0);
    gs_pairing_claim(&pairing, NULL, NULL, 3, 10.0 + GS_JOINED_SHOWN - 0.1);
    size_t count = gs_pairing_joined(&pairing, 10.0 + GS_JOINED_SHOWN + 0.05, players, GS_JOINED_MAX);
    check(count == 1, "the same seat claimed again renews its moment, once");
}

static void test_a_claim_with_no_player_clears_holds_but_seats_nobody(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    int players[GS_JOINED_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.5, 1, 10.0);
    gs_pairing_claim(&pairing, NULL, NULL, 0, 10.0);
    check(holds_at(&pairing, 10.0, out) == 0 && gs_pairing_joined(&pairing, 10.0, players, GS_JOINED_MAX) == 0,
          "a claim naming no seat ends the holds and shows nobody joined");
}

static void test_an_unsaid_seated_player_clears_no_anonymous_hold(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, NULL, NULL, 0.5, 0, 10.0);
    gs_pairing_seated(&pairing, NULL, NULL, 0);
    check(holds_at(&pairing, 10.0, out) == 1, "a seat with no number clears no nameless hold");
}

static void test_a_name_keyed_hold_is_cleared_by_name(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, NULL, "Xbox Pad", 0.5, 1, 10.0);
    gs_pairing_seated(&pairing, "/dev/input/event9", "Xbox Pad", 1);
    check(holds_at(&pairing, 10.0, out) == 0, "a hold known only by name is matched by name");
}

static void test_a_later_reading_with_no_player_keeps_the_known_seat(void) {
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    gs_hold out[GS_HOLDS_MAX];
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.2, 2, 10.0);
    gs_pairing_progress(&pairing, "/dev/input/event9", NULL, 0.3, 0, 10.05);
    holds_at(&pairing, 10.05, out);
    check(out[0].player == 2, "a reading that does not restate the seat keeps the one already known");
}

// --- the bar: edges of its arithmetic ------------------------------------------

static void test_a_zero_slide_moves_instantly(void) {
    gs_bar bar;
    gs_bar_init(&bar, 0.0);
    gs_bar_want(&bar, true, 1.0);
    bool down = gs_bar_position(&bar, 1.0) == 1.0;
    gs_bar_want(&bar, false, 1.0);
    check(down && gs_bar_gone(&bar, 1.0), "no slide time: down and up at once");
}

static void test_a_negative_slide_is_treated_as_zero(void) {
    gs_bar bar;
    gs_bar_init(&bar, -5.0);
    gs_bar_want(&bar, true, 1.0);
    check(gs_bar_position(&bar, 1.0) == 1.0, "a negative slide is instant, not undefined");
}

static void test_wanting_the_same_direction_again_does_not_restart_the_slide(void) {
    gs_bar bar;
    gs_bar_init(&bar, 0.25);
    gs_bar_want(&bar, true, 1.0);
    double halfway = gs_bar_position(&bar, 1.125);
    gs_bar_want(&bar, true, 1.125);
    check(near(gs_bar_position(&bar, 1.125), halfway, 1e-9) && gs_bar_position(&bar, 1.25) == 1.0,
          "asking again for where it is already going changes nothing");
}

static void test_a_clock_before_the_change_stays_in_range(void) {
    gs_bar bar;
    gs_bar_init(&bar, 0.25);
    gs_bar_want(&bar, true, 5.0);
    double p = gs_bar_position(&bar, 4.9);
    check(p >= 0.0 && p <= 1.0, "a clock read before the change is not a negative position");
}

static void test_a_double_reversal_still_settles(void) {
    gs_bar bar;
    gs_bar_init(&bar, 0.25);
    gs_bar_want(&bar, true, 1.0);
    gs_bar_want(&bar, false, 1.05);
    gs_bar_want(&bar, true, 1.06);
    double now = gs_bar_position(&bar, 1.06);
    check(now >= 0.0 && now <= 1.0 && gs_bar_position(&bar, 3.0) == 1.0,
          "three reversals in three frames stay in range and settle down");
}

// --- shapes: exact capacity -------------------------------------------------------

static void test_a_rect_at_exactly_its_capacity_is_drawn_whole(void) {
    gs_mesh mesh = fresh(4, 6);
    gs_colour white = {1, 1, 1, 1};
    gs_mesh_rect(&mesh, 0, 0, 10, 10, white);
    check(mesh.vertex_count == 4 && mesh.index_count == 6, "a rect that exactly fits is drawn in full");
}

static void test_short_of_room_draws_nothing_rather_than_half(void) {
    gs_colour white = {1, 1, 1, 1};
    gs_mesh short_vertex = fresh(3, 6), short_index = fresh(4, 5);
    gs_mesh_rect(&short_vertex, 0, 0, 10, 10, white);
    gs_mesh_rect(&short_index, 0, 0, 10, 10, white);
    check(short_vertex.vertex_count == 0 && short_index.vertex_count == 0,
          "one vertex or one index short: nothing, not a partial quad");
}

static void test_a_second_shape_is_dropped_once_the_first_fills_the_mesh(void) {
    gs_mesh mesh = fresh(4, 6);
    gs_colour white = {1, 1, 1, 1};
    gs_mesh_rect(&mesh, 0, 0, 10, 10, white);
    gs_mesh_rect(&mesh, 20, 20, 10, 10, white);
    check(mesh.vertex_count == 4, "the first shape drawn, the second dropped whole");
}

// --- padmap's lines, when they are strange ------------------------------------------

static bool parses_as(const char *line, gs_event_type type, gs_event *event) {
    return gs_event_parse(line, strlen(line), event) && event->type == type;
}

static void test_wrong_types_are_strangers_not_crashes(void) {
    gs_event event;
    check(parses_as("{\"event\":\"progress\",\"frac\":\"0.5\",\"node\":\"n\"}", GS_EVENT_OTHER, &event),
          "a fraction that is a string is not progress");
    check(parses_as("{\"event\":1,\"frac\":0.5}", GS_EVENT_OTHER, &event), "an event that is a number names nothing");
    check(parses_as("{}", GS_EVENT_OTHER, &event), "an empty object is nothing");
    check(parses_as("{\"event\":\"claim\",\"player\":{\"x\":1}}", GS_EVENT_CLAIM, &event) && event.player == 0,
          "a player that is an object is not a seat");
    check(parses_as("{\"event\":\"progress\",\"frac\":0.5,\"node\":{\"deep\":[1,{\"x\":true}]}}", GS_EVENT_PROGRESS,
                    &event) &&
              event.node[0] == '\0',
          "a node that is an object is simply empty");
}

static void test_a_huge_node_name_is_truncated_not_overrun(void) {
    static char line[GS_KEY_MAX * 20];
    char *p = line;
    p += sprintf(p, "{\"event\":\"progress\",\"frac\":0.1,\"node\":\"");
    for (int i = 0; i < GS_KEY_MAX * 10; i++) *p++ = 'a';
    p += sprintf(p, "\"}");
    gs_event event;
    check(gs_event_parse(line, (size_t)(p - line), &event) && strlen(event.node) == GS_KEY_MAX - 1,
          "an oversize node is cut to the key's room");
}

static void test_state_seats_are_capped_and_strangers_skipped(void) {
    char line[2048];
    char *p = line;
    p += sprintf(p, "{\"event\":\"state\",\"players\":[");
    for (int i = 0; i < GS_SEATED_MAX + 5; i++) p += sprintf(p, "%s{\"player\":%d,\"node\":\"n%d\"}", i ? "," : "", i + 1, i);
    sprintf(p, "]}");
    gs_event event;
    check(parses_as(line, GS_EVENT_STATE, &event) && event.seated_count == GS_SEATED_MAX, "more seats than fit are capped");
    check(parses_as("{\"event\":\"state\",\"players\":[1,\"x\",null,{\"player\":2,\"node\":\"n\"}]}", GS_EVENT_STATE,
                    &event) &&
              event.seated_count == 1,
          "entries that are not objects are skipped");
    check(parses_as("{\"event\":\"state\",\"players\":\"nope\"}", GS_EVENT_STATE, &event) && event.seated_count == 0,
          "a players field that is not a list seats nobody");
}

static void test_a_bare_value_is_not_an_event(void) {
    gs_event event;
    check(!gs_event_parse("null", 4, &event) && !gs_event_parse("42", 2, &event) &&
              !gs_event_parse("\"hi\"", 4, &event) && !gs_event_parse("", 0, &event),
          "null, a number, a string or nothing is not an object");
}

// --- the link: line endings, boundaries and the real connect ----------------------

static int feed_text(const char *text) {
    gs_link link;
    gs_link_init(&link, "/nonexistent");
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    return gs_link_feed(&link, text, strlen(text), &pairing, 10.0);
}

static void test_line_endings_and_blank_lines(void) {
    check(feed_text("{\"event\":\"progress\",\"frac\":0.4,\"node\":\"n\"}\r\n") == 1, "a CRLF line still parses");
    check(feed_text("{\"event\":\"progress\",\"frac\":0.1,\"node\":\"a\"}\n"
                    "{\"event\":\"progress\",\"frac\":0.2,\"node\":\"b\"}\n"
                    "{\"event\":\"progress\",\"frac\":0.3,\"node\":\"c\"}\n") == 3,
          "three lines in one read all apply");
    check(feed_text("\n\n{\"event\":\"progress\",\"frac\":0.1,\"node\":\"a\"}\n\n") == 1, "blank lines between are skipped");
}

static void test_a_buffer_full_with_no_newline_is_dropped_and_recovered_from(void) {
    static char exact[GS_LINK_BUFFER];
    memset(exact, 'x', sizeof exact);
    gs_link link;
    gs_link_init(&link, "/nonexistent");
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    int first = gs_link_feed(&link, exact, sizeof exact, &pairing, 10.0);
    const char *after = "\n{\"event\":\"progress\",\"frac\":0.3,\"node\":\"n\"}\n";
    int second = gs_link_feed(&link, after, strlen(after), &pairing, 10.0);
    check(first == 0 && second == 1, "a buffer filled exactly without a newline is skipped, and the next line read");
}

static void test_a_line_exactly_the_buffer_size_still_parses(void) {
    static char line[GS_LINK_BUFFER + 1];
    const char *prefix = "{\"event\":\"progress\",\"frac\":0.5,\"node\":\"";
    const char *suffix = "\"}";
    size_t pad = GS_LINK_BUFFER - (strlen(prefix) + strlen(suffix) + 1);
    char *p = line;
    memcpy(p, prefix, strlen(prefix));
    p += strlen(prefix);
    memset(p, 'a', pad);
    p += pad;
    memcpy(p, suffix, strlen(suffix));
    p += strlen(suffix);
    *p++ = '\n';
    gs_link link;
    gs_link_init(&link, "/nonexistent");
    gs_pairing pairing;
    gs_pairing_init(&pairing, 1.5);
    check((size_t)(p - line) == GS_LINK_BUFFER && gs_link_feed(&link, line, GS_LINK_BUFFER, &pairing, 10.0) == 1,
          "a whole line that exactly fills the buffer still parses");
}

static void test_tick_connects_to_a_listening_socket(void) {
    char path[108];
    snprintf(path, sizeof path, "/tmp/gotg-overlay-test-%d.sock", (int)getpid());
    unlink(path);
    int server = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un address;
    memset(&address, 0, sizeof address);
    address.sun_family = AF_UNIX;
    strncpy(address.sun_path, path, sizeof address.sun_path - 1);
    if (server < 0 || bind(server, (struct sockaddr *)&address, sizeof address) != 0 || listen(server, 1) != 0) {
        check(false, "a listening socket for the test");
        if (server >= 0) close(server);
        unlink(path);
        return;
    }
    gs_link link;
    gs_link_init(&link, path);
    gs_link_tick(&link, 0.0);
    check(gs_link_fd(&link) >= 0, "tick connects to a socket that is listening, as this user");
    gs_link_close(&link);
    check(gs_link_fd(&link) == -1, "and close drops it");
    close(server);
    unlink(path);
}

static void test_tick_on_nothing_or_nonsense_stays_disconnected(void) {
    gs_link missing;
    gs_link_init(&missing, "/nonexistent-gotg-overlay-test.sock");
    gs_link_tick(&missing, 0.0);
    gs_link_tick(&missing, 0.01);
    char huge[1024];
    memset(huge, 'a', sizeof huge - 1);
    huge[sizeof huge - 1] = '\0';
    gs_link overlong;
    gs_link_init(&overlong, huge);
    gs_link_tick(&overlong, 0.0);
    check(gs_link_fd(&missing) == -1 && gs_link_fd(&overlong) == -1,
          "nothing listening, or a path too long for a socket: not connected, not overrun");
}

// --- the scene at its fullest ------------------------------------------------------

static void test_a_full_scene_stays_centred_and_within_its_room(void) {
    float bar = gs_bar_height(800);
    size_t count = GS_HOLDS_MAX + GS_JOINED_MAX;
    check(near(gs_item_x(1280, bar, 0, count) + gs_item_x(1280, bar, count - 1, count), 1280.0, 1e-3),
          "twelve items are still mirrored about the middle");
    gs_mesh mesh = fresh(ROOM, GS_MESH_INDICES);
    float fractions[GS_HOLDS_MAX];
    int32_t players[GS_HOLDS_MAX], joined[GS_JOINED_MAX];
    for (int i = 0; i < GS_HOLDS_MAX; i++) {
        fractions[i] = 0.3f;
        players[i] = i + 1;
    }
    for (int i = 0; i < GS_JOINED_MAX; i++) joined[i] = i + 1;
    gs_scene scene = {.width = 1280,
                      .bar_height = bar,
                      .position = 1.0,
                      .fractions = fractions,
                      .players = players,
                      .hold_count = GS_HOLDS_MAX,
                      .joined = joined,
                      .joined_count = GS_JOINED_MAX,
                      .clock = 0.5};
    gs_scene_build(&scene, &mesh);
    // Short of the room, so the last badge was not cut off by running out.
    check(mesh.vertex_count > 0 && mesh.vertex_count < ROOM, "eight holding and four seated draw within the room");
}

int main(void) {
    test_a_named_hold_survives_the_daemon_pausing_to_rescan();
    test_a_named_release_is_immediate();
    test_a_daemon_that_dies_mid_hold_empties_the_bar_eventually();
    test_a_nameless_reading_still_ends_with_silence();
    test_the_first_press_is_drawn_first();
    test_a_fill_that_goes_backwards_is_a_new_press_at_the_back();
    test_the_sweep_never_steps_backwards();
    test_a_claim_clears_the_holds_and_shows_the_seat_for_a_moment();
    test_a_claim_leaves_the_other_holds_filling();
    test_a_state_drops_only_the_hold_that_became_a_seat();
    test_nothing_to_show_is_not_busy();
    test_the_bar_slides_down_and_back();
    test_turning_round_mid_slide_does_not_jump();
    test_an_arc_starts_at_twelve_and_turns_clockwise();
    test_an_arc_has_a_soft_edge();
    test_nothing_is_drawn_for_nothing();
    test_a_full_mesh_draws_less_rather_than_overflowing();
    test_a_bar_out_of_sight_draws_nothing();
    test_the_bar_comes_down_from_the_top_edge();
    test_the_exit_ring_outranks_joining();
    test_items_are_centred_and_evenly_spaced();
    test_seats_are_the_picker_s_colours();
    test_a_progress_line_is_read();
    test_a_state_names_who_is_seated();
    test_nonsense_is_refused_and_strangers_ignored();
    test_lines_split_across_reads_are_joined();
    test_a_line_too_long_is_skipped_whole();
    test_a_socket_is_read_and_its_closing_noticed();
    test_a_frame_carries_what_the_bar_draws();
    test_a_frame_fits_a_pipe_write_whole();
    test_a_frame_that_is_not_one_is_refused();
    test_packing_more_than_fits_is_clamped();
    test_no_runtime_directory_is_no_link();

    test_a_ninth_hold_evicts_the_oldest();
    test_a_fifth_joined_seat_evicts_the_oldest();
    test_a_repeated_claim_refreshes_rather_than_duplicates();
    test_a_claim_with_no_player_clears_holds_but_seats_nobody();
    test_an_unsaid_seated_player_clears_no_anonymous_hold();
    test_a_name_keyed_hold_is_cleared_by_name();
    test_a_later_reading_with_no_player_keeps_the_known_seat();
    test_a_zero_slide_moves_instantly();
    test_a_negative_slide_is_treated_as_zero();
    test_wanting_the_same_direction_again_does_not_restart_the_slide();
    test_a_clock_before_the_change_stays_in_range();
    test_a_double_reversal_still_settles();
    test_a_rect_at_exactly_its_capacity_is_drawn_whole();
    test_short_of_room_draws_nothing_rather_than_half();
    test_a_second_shape_is_dropped_once_the_first_fills_the_mesh();
    test_wrong_types_are_strangers_not_crashes();
    test_a_huge_node_name_is_truncated_not_overrun();
    test_state_seats_are_capped_and_strangers_skipped();
    test_a_bare_value_is_not_an_event();
    test_line_endings_and_blank_lines();
    test_a_buffer_full_with_no_newline_is_dropped_and_recovered_from();
    test_a_line_exactly_the_buffer_size_still_parses();
    test_tick_connects_to_a_listening_socket();
    test_tick_on_nothing_or_nonsense_stays_disconnected();
    test_a_full_scene_stays_centred_and_within_its_room();

    printf("%s\n", failures ? "FAILED" : "all good");
    return failures ? 1 : 0;
}
