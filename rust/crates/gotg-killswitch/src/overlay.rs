//! The window the bar is drawn in, over whatever game is running.
//!
//! "Over the game" is a different request to every display server this runs
//! under, so there are three ways in and the first that fits is taken:
//!
//!   gamescope (a Deck in Game Mode)
//!       An X11 window with GAMESCOPE_EXTERNAL_OVERLAY set: gamescope's one
//!       external-overlay slot, which mangoapp uses too. It sits above the
//!       game and below Steam's own overlay (steamcompmgr.hpp: zpos 2 vs 3).
//!   a compositor with layer-shell (sway, Hyprland, KDE)
//!       A layer-shell surface on the overlay layer, which sway stacks above
//!       fullscreen windows. No input and no keyboard, so a click lands on
//!       the game.
//!   anything else with X11 (cage, which QA runs games in, has no layer-shell)
//!       An override-redirect window with an empty input shape: wlroots draws
//!       unmanaged X11 windows above fullscreen ones, and nothing clicks it.
//!
//! The window exists only while the bar is on screen: a hidden overlay that
//! stays mapped costs a fullscreen game its direct scanout on wlroots, and
//! holds gamescope's slot from mangoapp.

use std::ffi::{CStr, c_int, c_void};
use std::fmt;
use std::time::{Duration, Instant};

use sdl3_sys::everything::*;
use wayland_client::backend::{Backend, ObjectId};
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::wl_surface::WlSurface;
use wayland_client::protocol::{wl_compositor::WlCompositor, wl_region::WlRegion, wl_registry::WlRegistry};
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_shell_v1::{Layer, ZwlrLayerShellV1};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::{
    self, Anchor, KeyboardInteractivity, ZwlrLayerSurfaceV1,
};
use x11rb::connection::Connection as _;
use x11rb::protocol::shape::{self, ConnectionExt as _};
use x11rb::protocol::xproto::{
    AtomEnum, ClipOrdering, ConfigureWindowAux, ConnectionExt as _, PropMode, StackMode,
};
use x11rb::rust_connection::RustConnection;
use x11rb::wrapper::ConnectionExt as _;

use std::collections::HashMap;

use crate::icons;
use crate::scene::{Drawing, Sprite, bar_height};
use crate::shapes::{Colour, Mesh, Vertex, wedge};

/// Which way in was taken.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Gamescope,
    LayerShell,
    X11,
}

impl fmt::Display for Kind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Kind::Gamescope => "gamescope",
            Kind::LayerShell => "layer-shell",
            Kind::X11 => "x11",
        })
    }
}

/// Why there is no window: said once, on stderr, by the painter. A painter
/// that exits in silence looks exactly like a bar nobody asked for; on a Deck
/// it was "Could not get EGL display" from every GPU renderer.
#[derive(Debug)]
pub struct NoOverlay {
    pub kind: Kind,
    pub why: String,
}

impl fmt::Display for NoOverlay {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "no overlay ({}): {}", self.kind, self.why)
    }
}

// --- which way in --------------------------------------------------------------

#[derive(Debug)]
struct Probe;

impl Dispatch<WlRegistry, GlobalListContents> for Probe {
    fn event(
        _: &mut Self,
        _: &WlRegistry,
        _: <WlRegistry as Proxy>::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

/// Whether the Wayland compositor here offers layer-shell. Asked on a
/// connection of our own, before SDL picks a video driver: the answer decides
/// which driver to ask for.
fn has_layer_shell() -> bool {
    if std::env::var_os("WAYLAND_DISPLAY").is_none_or(|name| name.is_empty()) {
        return false;
    }
    let Ok(connection) = Connection::connect_to_env() else {
        return false;
    };
    let Ok((globals, _queue)) = registry_queue_init::<Probe>(&connection) else {
        return false;
    };
    globals.contents().with_list(|list| {
        list.iter()
            .any(|global| global.interface == ZwlrLayerShellV1::interface().name)
    })
}

fn choose() -> Kind {
    // Inside gamescope, Xwayland rather than its wayland socket: the overlay
    // property is an X11 one, and a native wayland surface there is a second
    // window gamescope will not put over the game.
    if std::env::var_os("GAMESCOPE_WAYLAND_DISPLAY").is_some() && std::env::var_os("DISPLAY").is_some() {
        return Kind::Gamescope;
    }
    if has_layer_shell() {
        Kind::LayerShell
    } else {
        Kind::X11
    }
}

// --- layer-shell ----------------------------------------------------------------

/// What the compositor has said about our layer surface.
#[derive(Debug, Default)]
struct LayerState {
    width: u32,
    configured: bool,
    closed: bool,
}

impl Dispatch<WlRegistry, GlobalListContents> for LayerState {
    fn event(
        _: &mut Self,
        _: &WlRegistry,
        _: <WlRegistry as Proxy>::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<WlCompositor, ()> for LayerState {
    fn event(
        _: &mut Self,
        _: &WlCompositor,
        _: <WlCompositor as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<WlRegion, ()> for LayerState {
    fn event(
        _: &mut Self,
        _: &WlRegion,
        _: <WlRegion as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<ZwlrLayerShellV1, ()> for LayerState {
    fn event(
        _: &mut Self,
        _: &ZwlrLayerShellV1,
        _: <ZwlrLayerShellV1 as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<ZwlrLayerSurfaceV1, ()> for LayerState {
    fn event(
        state: &mut Self,
        layer: &ZwlrLayerSurfaceV1,
        event: zwlr_layer_surface_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            zwlr_layer_surface_v1::Event::Configure { serial, width, .. } => {
                layer.ack_configure(serial);
                state.width = width;
                state.configured = true;
            }
            zwlr_layer_surface_v1::Event::Closed => state.closed = true,
            _ => {}
        }
    }
}

/// Our objects on SDL's Wayland connection, on a queue of our own.
struct LayerSurface {
    connection: Connection,
    queue: EventQueue<LayerState>,
    state: LayerState,
    shell: ZwlrLayerShellV1,
    layer: ZwlrLayerSurfaceV1,
    /// The width SDL was last told: a custom surface is told nothing about
    /// its size by the compositor, so SDL has to be.
    width: u32,
}

impl LayerSurface {
    /// Make SDL's surface a layer surface on the overlay layer, anchored to
    /// the top edge, `bar` pixels tall.
    ///
    /// # Safety
    /// `display` and `surface` are SDL's live wl_display and wl_surface, and
    /// outlive what this returns.
    unsafe fn new(display: *mut c_void, surface: *mut c_void, bar: u32) -> Result<Self, String> {
        // SAFETY: per this function's contract; foreign objects are left to SDL.
        let backend = unsafe { Backend::from_foreign_display(display.cast()) };
        let connection = Connection::from_backend(backend);
        let (globals, mut queue) =
            registry_queue_init::<LayerState>(&connection).map_err(|e| e.to_string())?;
        let handle = queue.handle();
        let shell: ZwlrLayerShellV1 = globals.bind(&handle, 1..=4, ()).map_err(|e| e.to_string())?;
        let compositor: WlCompositor = globals.bind(&handle, 1..=1, ()).map_err(|e| e.to_string())?;
        // SAFETY: `surface` is a live wl_surface proxy, per the contract.
        let id = unsafe { ObjectId::from_ptr(WlSurface::interface(), surface.cast()) }
            .map_err(|e| e.to_string())?;
        let surface = WlSurface::from_id(&connection, id).map_err(|e| e.to_string())?;

        let layer =
            shell.get_layer_surface(&surface, None, Layer::Overlay, "gotg-overlay".into(), &handle, ());
        layer.set_anchor(Anchor::Top | Anchor::Left | Anchor::Right);
        layer.set_size(0, bar);
        // -1: neither reserve room nor be pushed aside by a panel's
        // reservation. The bar goes over the game, not beside it.
        layer.set_exclusive_zone(-1);
        layer.set_keyboard_interactivity(KeyboardInteractivity::None);
        // An empty input region: the pointer goes through to the game.
        let nothing = compositor.create_region(&handle, ());
        surface.set_input_region(Some(&nothing));
        nothing.destroy();
        surface.commit();

        let mut state = LayerState::default();
        queue.roundtrip(&mut state).map_err(|e| e.to_string())?;
        if !state.configured || state.closed {
            layer.destroy();
            shell.destroy();
            return Err("the compositor did not configure the layer surface".into());
        }
        Ok(Self {
            connection,
            queue,
            state,
            shell,
            layer,
            width: 0,
        })
    }

    /// Whatever the compositor has said since: a new width to pass on to SDL.
    fn pump(&mut self) -> Option<u32> {
        let _ = self.queue.dispatch_pending(&mut self.state);
        (self.state.width > 0 && self.state.width != self.width).then(|| {
            self.width = self.state.width;
            self.width
        })
    }
}

impl Drop for LayerSurface {
    fn drop(&mut self) {
        // Before SDL destroys the surface under it.
        self.layer.destroy();
        self.shell.destroy();
        let _ = self.connection.flush();
    }
}

// --- X11 -------------------------------------------------------------------------

/// A connection of our own to the X server SDL's window is on: properties,
/// shapes and stacking are anybody's to set on a window they can name.
struct X11 {
    connection: RustConnection,
    window: u32,
    raised_at: Option<Instant>,
}

impl X11 {
    fn new(window: u32) -> Option<Self> {
        let (connection, _) = RustConnection::connect(None).ok()?;
        Some(Self {
            connection,
            window,
            raised_at: None,
        })
    }

    /// gamescope's overlay slot.
    fn claim_gamescope_slot(&self) {
        let claimed = (|| -> Result<(), Box<dyn std::error::Error>> {
            let atom = self
                .connection
                .intern_atom(false, b"GAMESCOPE_EXTERNAL_OVERLAY")?
                .reply()?
                .atom;
            self.connection.change_property32(
                PropMode::REPLACE,
                self.window,
                atom,
                AtomEnum::CARDINAL,
                &[1],
            )?;
            self.connection.flush()?;
            Ok(())
        })();
        if let Err(error) = claimed {
            eprintln!("gotg-killswitch: gamescope's overlay slot: {error}");
        }
    }

    /// Clicks go through: an input shape with nothing in it.
    fn let_input_through(&self) {
        let _ = self.connection.shape_rectangles(
            shape::SO::SET,
            shape::SK::INPUT,
            ClipOrdering::UNSORTED,
            self.window,
            0,
            0,
            &[],
        );
        let _ = self.connection.flush();
    }

    /// Override-redirect windows stack by who was raised last, so a game
    /// that goes fullscreen or raises itself after the bar came down would
    /// cover it. Twice a second rather than every frame: a restack is the X
    /// server's and the compositor's work, and the bar is only ever covered
    /// by something new.
    fn keep_on_top(&mut self) {
        let now = Instant::now();
        if self
            .raised_at
            .is_some_and(|at| now - at < Duration::from_millis(500))
        {
            return;
        }
        self.raised_at = Some(now);
        let aux = ConfigureWindowAux::new().stack_mode(StackMode::ABOVE);
        let _ = self.connection.configure_window(self.window, &aux);
        let _ = self.connection.flush();
    }
}

// --- the window -------------------------------------------------------------------

/// The bar's window, open.
pub struct Overlay {
    kind: Kind,
    window: *mut SDL_Window,
    renderer: *mut SDL_Renderer,
    screen_height: i32,
    /// In pixels, worked out once, where the window is bigger than the bar.
    bar_height: f32,
    vsync: bool,
    layer: Option<LayerSurface>,
    x11: Option<X11>,
    /// Each drawing at each height it has been drawn at, as a white
    /// silhouette and its width: rasterised once, tinted per seat.
    textures: HashMap<(u8, u32), (*mut SDL_Texture, u32)>,
    /// Scratch for a reveal's triangles, kept so a frame allocates nothing.
    swept: Vec<[f32; 2]>,
    vertices: Vec<SDL_Vertex>,
}

impl fmt::Debug for Overlay {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Overlay")
            .field("kind", &self.kind)
            .field("vsync", &self.vsync)
            .finish_non_exhaustive()
    }
}

/// SDL_GetError, copied.
fn sdl_error() -> String {
    // SAFETY: SDL_GetError never returns null.
    unsafe { CStr::from_ptr(SDL_GetError()) }
        .to_string_lossy()
        .into_owned()
}

impl Overlay {
    /// The window, or why there is none -- in which case the painter exits
    /// and the kill switch carries on without a bar.
    pub fn open() -> Result<Self, NoOverlay> {
        let kind = choose();
        let fail = |why: String| NoOverlay { kind, why };
        // SAFETY: SDL is used from this one thread; every pointer below is
        // SDL's own or a C string literal.
        unsafe {
            let driver = if kind == Kind::LayerShell {
                c"wayland"
            } else {
                c"x11"
            };
            SDL_SetHint(SDL_HINT_VIDEO_DRIVER, driver.as_ptr());
            if kind == Kind::X11 {
                SDL_SetHint(SDL_HINT_X11_FORCE_OVERRIDE_REDIRECT, c"1".as_ptr());
            }
            if !SDL_InitSubSystem(SDL_INIT_VIDEO) {
                return Err(fail(sdl_error()));
            }
            let mut overlay = Self {
                kind,
                window: std::ptr::null_mut(),
                renderer: std::ptr::null_mut(),
                screen_height: 800,
                bar_height: 0.0,
                vsync: false,
                layer: None,
                x11: None,
                textures: HashMap::new(),
                swept: Vec::new(),
                vertices: Vec::new(),
            };
            let mut screen = SDL_Rect {
                x: 0,
                y: 0,
                w: 1280,
                h: 800,
            };
            SDL_GetDisplayBounds(SDL_GetPrimaryDisplay(), &mut screen);
            overlay.screen_height = screen.h;
            let bar = bar_height(screen.h) as i64;

            let props = SDL_CreateProperties();
            SDL_SetStringProperty(
                props,
                SDL_PROP_WINDOW_CREATE_TITLE_STRING,
                c"gotg overlay".as_ptr(),
            );
            for flag in [
                SDL_PROP_WINDOW_CREATE_TRANSPARENT_BOOLEAN,
                SDL_PROP_WINDOW_CREATE_BORDERLESS_BOOLEAN,
                SDL_PROP_WINDOW_CREATE_ALWAYS_ON_TOP_BOOLEAN,
                SDL_PROP_WINDOW_CREATE_UTILITY_BOOLEAN,
            ] {
                SDL_SetBooleanProperty(props, flag, true);
            }
            SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_FOCUSABLE_BOOLEAN, false);
            if kind == Kind::LayerShell {
                SDL_SetBooleanProperty(
                    props,
                    SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN,
                    true,
                );
                SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_HIGH_PIXEL_DENSITY_BOOLEAN, true);
                SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_WIDTH_NUMBER, i64::from(screen.w));
                SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_HEIGHT_NUMBER, bar);
            } else {
                // gamescope paints its overlay at the screen's size and
                // origin, as mangoapp's is; elsewhere the window is the bar's strip.
                let height = if kind == Kind::Gamescope {
                    i64::from(screen.h)
                } else {
                    bar
                };
                SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_X_NUMBER, i64::from(screen.x));
                SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_Y_NUMBER, i64::from(screen.y));
                SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_WIDTH_NUMBER, i64::from(screen.w));
                SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_HEIGHT_NUMBER, height);
            }
            overlay.window = SDL_CreateWindowWithProperties(props);
            SDL_DestroyProperties(props);
            if overlay.window.is_null() {
                return Err(fail(sdl_error()));
            }

            overlay.renderer = SDL_CreateRenderer(overlay.window, std::ptr::null());
            // No GPU userspace this program can load (a nix build on SteamOS
            // run without the package's foreign-GL script): software draws a
            // strip of flat shapes in well under a frame. X11 only, in
            // practice -- SDL's Wayland backend has no framebuffer without EGL.
            if overlay.renderer.is_null() {
                overlay.renderer = SDL_CreateRenderer(overlay.window, SDL_SOFTWARE_RENDERER);
            }
            if overlay.renderer.is_null() {
                return Err(fail(sdl_error()));
            }
            SDL_SetRenderDrawBlendMode(overlay.renderer, SDL_BLENDMODE_BLEND);
            // On the panel's beat, where the driver will: the slide is the one
            // thing here that moves, and a slide timed by a sleep steps.
            overlay.vsync = SDL_SetRenderVSync(overlay.renderer, 1);

            let window_props = SDL_GetWindowProperties(overlay.window);
            let x11_window = || {
                let id = SDL_GetNumberProperty(window_props, SDL_PROP_WINDOW_X11_WINDOW_NUMBER, 0);
                u32::try_from(id).ok().filter(|&id| id != 0).and_then(X11::new)
            };
            match kind {
                Kind::Gamescope => {
                    // gamescope's window is the whole screen: the bar is sized for it.
                    overlay.bar_height = bar_height(screen.h);
                    overlay.x11 = x11_window();
                    if let Some(x11) = &overlay.x11 {
                        x11.claim_gamescope_slot();
                    }
                }
                Kind::LayerShell => {
                    let display = SDL_GetPointerProperty(
                        window_props,
                        SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER,
                        std::ptr::null_mut(),
                    );
                    let surface = SDL_GetPointerProperty(
                        window_props,
                        SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER,
                        std::ptr::null_mut(),
                    );
                    if display.is_null() || surface.is_null() {
                        return Err(fail("SDL gave no Wayland surface".into()));
                    }
                    let layer = LayerSurface::new(display, surface, bar as u32).map_err(fail)?;
                    overlay.layer = Some(layer);
                    overlay.follow_the_compositor();
                }
                Kind::X11 => {
                    overlay.bar_height = bar as f32;
                    overlay.x11 = x11_window();
                    if let Some(x11) = &mut overlay.x11 {
                        x11.let_input_through();
                        x11.keep_on_top();
                    }
                }
            }
            Ok(overlay)
        }
    }

    pub fn kind(&self) -> Kind {
        self.kind
    }

    /// Which SDL renderer draws it: "software" is the fallback where no GPU
    /// userspace could be loaded.
    pub fn renderer_name(&self) -> String {
        // SAFETY: the renderer is live; SDL's name is a static string or null.
        let name = unsafe { SDL_GetRendererName(self.renderer) };
        if name.is_null() {
            return "none".into();
        }
        // SAFETY: non-null and NUL-terminated.
        unsafe { CStr::from_ptr(name) }.to_string_lossy().into_owned()
    }

    /// Whether presenting waits for the panel. Where it does not, the painter
    /// paces itself, or it would draw as fast as the driver lets it.
    pub fn vsync(&self) -> bool {
        self.vsync
    }

    /// A layer surface's width is the compositor's to say, and a custom
    /// surface is told nothing about it: SDL is told here.
    fn follow_the_compositor(&mut self) {
        let Some(width) = self.layer.as_mut().and_then(LayerSurface::pump) else {
            return;
        };
        let bar = bar_height(self.screen_height) as c_int;
        // SAFETY: the window is live.
        unsafe { SDL_SetWindowSize(self.window, width as c_int, bar) };
    }

    /// The surface, in pixels, and the bar's height on it. On layer-shell the
    /// surface is the bar, so its height in pixels is the bar's however the
    /// output is scaled -- read live, since the compositor decides it.
    pub fn size(&self) -> (i32, f32) {
        let (mut width, mut height): (c_int, c_int) = (0, 0);
        // SAFETY: the renderer is live and the pointers are to locals.
        unsafe { SDL_GetRenderOutputSize(self.renderer, &mut width, &mut height) };
        let bar = if self.kind == Kind::LayerShell {
            height as f32
        } else {
            self.bar_height
        };
        (width, bar)
    }

    /// Whether the compositor has taken the surface away.
    fn closed(&self) -> bool {
        self.layer.as_ref().is_some_and(|layer| layer.state.closed)
    }

    /// Flat shapes, as SDL triangles with their colours.
    fn draw_mesh(&self, mesh: &Mesh) {
        if mesh.vertices.is_empty() || mesh.indices.is_empty() {
            return;
        }
        // SAFETY: the renderer is live; the vertex and index slices outlive
        // the call, and their strides and counts are their own.
        unsafe {
            let stride = std::mem::size_of::<Vertex>() as c_int;
            let first = mesh.vertices.as_ptr();
            SDL_RenderGeometryRaw(
                self.renderer,
                std::ptr::null_mut(),
                (&raw const (*first).x).cast::<f32>(),
                stride,
                (&raw const (*first).colour).cast::<SDL_FColor>(),
                stride,
                std::ptr::null(),
                0,
                mesh.vertices.len() as c_int,
                mesh.indices.as_ptr().cast::<c_void>(),
                mesh.indices.len() as c_int,
                std::mem::size_of::<i32>() as c_int,
            );
        }
    }

    /// A drawing as a texture at this height, made the first time it is asked
    /// for. None where it cannot be drawn: the bar carries on without it.
    fn texture(&mut self, icon: u8, height: u32) -> Option<(*mut SDL_Texture, u32)> {
        if let Some(&found) = self.textures.get(&(icon, height)) {
            return Some(found);
        }
        let (width, pixels) =
            icons::silhouette(icon, height).or_else(|| icons::silhouette(icons::FALLBACK, height))?;
        // SAFETY: the renderer is live; the pixels are `width` x `height`
        // RGBA, `width * 4` bytes a row, and outlive the upload.
        let texture = unsafe {
            let texture = SDL_CreateTexture(
                self.renderer,
                SDL_PIXELFORMAT_RGBA32,
                SDL_TEXTUREACCESS_STATIC,
                width as c_int,
                height as c_int,
            );
            if texture.is_null() {
                return None;
            }
            SDL_UpdateTexture(
                texture,
                std::ptr::null(),
                pixels.as_ptr().cast(),
                (width * 4) as c_int,
            );
            SDL_SetTextureBlendMode(texture, SDL_BLENDMODE_BLEND);
            texture
        };
        self.textures.insert((icon, height), (texture, width));
        Some((texture, width))
    }

    /// One pad's drawing: the dim copy where it is not yet revealed, then the
    /// swept part in its seat's colour.
    fn draw_sprite(&mut self, sprite: &Sprite) {
        let height = sprite.height.round().max(1.0) as u32;
        let Some((texture, width)) = self.texture(sprite.icon, height) else {
            return;
        };
        let (w, h) = (width as f32, height as f32);
        let (left, top) = ((sprite.cx - w / 2.0).round(), (sprite.cy - h / 2.0).round());
        let byte = |channel: f32| (channel.clamp(0.0, 1.0) * 255.0).round() as u8;
        // SAFETY: the renderer and texture are live; the rect and vertices
        // are locals and scratch owned here, alive for each call.
        unsafe {
            if sprite.revealed < 1.0 {
                let under = sprite.under;
                SDL_SetTextureColorMod(texture, byte(under.r), byte(under.g), byte(under.b));
                SDL_SetTextureAlphaMod(texture, byte(under.a));
                let whole = SDL_FRect {
                    x: left,
                    y: top,
                    w,
                    h,
                };
                SDL_RenderTexture(self.renderer, texture, std::ptr::null(), &whole);
            }
            // The vertex colour does the tinting from here.
            SDL_SetTextureColorMod(texture, 255, 255, 255);
            SDL_SetTextureAlphaMod(texture, 255);
        }
        self.swept.clear();
        wedge(left, top, w, h, sprite.revealed, &mut self.swept);
        if self.swept.is_empty() {
            return;
        }
        let Colour { r, g, b, a } = sprite.colour;
        self.vertices.clear();
        self.vertices.extend(self.swept.iter().map(|&[x, y]| SDL_Vertex {
            position: SDL_FPoint { x, y },
            color: SDL_FColor { r, g, b, a },
            tex_coord: SDL_FPoint {
                x: (x - left) / w,
                y: (y - top) / h,
            },
        }));
        // SAFETY: as above; the vertex slice outlives the call.
        unsafe {
            SDL_RenderGeometry(
                self.renderer,
                texture,
                self.vertices.as_ptr(),
                self.vertices.len() as c_int,
                std::ptr::null(),
                0,
            );
        }
    }

    /// One frame: cleared to nothing, the bar, the pads' drawings, what goes
    /// over them, presented.
    pub fn draw(&mut self, drawing: &Drawing) {
        self.follow_the_compositor();
        if self.closed() {
            return;
        }
        // SAFETY: the renderer is live.
        unsafe {
            SDL_SetRenderDrawColor(self.renderer, 0, 0, 0, 0);
            SDL_RenderClear(self.renderer);
        }
        self.draw_mesh(&drawing.under);
        for sprite in &drawing.sprites {
            self.draw_sprite(sprite);
        }
        self.draw_mesh(&drawing.over);
        // SAFETY: the renderer is live.
        unsafe { SDL_RenderPresent(self.renderer) };
        if self.kind == Kind::X11
            && let Some(x11) = &mut self.x11
        {
            x11.keep_on_top();
        }
    }
}

impl Drop for Overlay {
    fn drop(&mut self) {
        self.layer = None;
        self.x11 = None;
        // SAFETY: each is SDL's, destroyed once: textures before their
        // renderer, the renderer before its window.
        unsafe {
            for (texture, _) in self.textures.values() {
                SDL_DestroyTexture(*texture);
            }
            if !self.renderer.is_null() {
                SDL_DestroyRenderer(self.renderer);
            }
            if !self.window.is_null() {
                SDL_DestroyWindow(self.window);
            }
            SDL_QuitSubSystem(SDL_INIT_VIDEO);
        }
    }
}
