/**
 * 3D Letter Swap（React Bits Pro 同款，本地适配版）：
 * 逐字符 3D 翻转 + 可选模糊过渡（stagger 交错动画）。
 * 支持两种模式：
 *   1. 装饰自翻转（原版行为，不传 swapText）：hover 时每个字符翻到背面
 *      再瞬间复位，可用 front/backFaceClassName 给正背面分别染色；
 *   2. A↔B 文字切换（传 swapText，双层架构，同 React Bits Letter Swap）：
 *      hover 将 children 文字逐字符翻出（rotateX→90° + 模糊淡出），
 *      swapText 文字逐字符翻入（-90°→0°）；移开后反向翻回。
 *      两层文字各自独立自然排版（宽度按自身字符计算，中英文混排不会
 *      产生额外字距），背层绝对定位整体居中叠加。
 * 原版依赖 Tailwind 工具类（inline-block / relative / absolute / h-lh / sr-only），
 * 此处改为内联样式；letter-3d-swap-* 类名保留，作为 motion 的动画选择器；
 * 动画逻辑与原版一致。
 * 依赖 npm 包：motion
 */
import React, {
  ElementType,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  ComponentPropsWithoutRef,
} from "react";
import {
  AnimationOptions,
  useAnimate,
  ValueAnimationTransition,
} from "motion/react";

import { cn } from "@/lib/utils";

const splitIntoCharacters = (text: string): string[] => {
  if (typeof Intl !== "undefined" && "Segmenter" in Intl) {
    const segmenter = new Intl.Segmenter("en", { granularity: "grapheme" });
    return Array.from(segmenter.segment(text), ({ segment }) => segment);
  }
  return Array.from(text);
};

const extractTextFromChildren = (
  children: React.ReactNode,
): string | undefined => {
  if (children == null) return "";

  if (typeof children === "string") return children;

  if (typeof children === "number") return String(children);

  if (Array.isArray(children)) {
    return children.map(extractTextFromChildren).join("");
  }

  if (React.isValidElement(children)) {
    const element = children as React.ReactElement<{
      children?: React.ReactNode;
    }>;
    const childText = element.props.children;

    if (childText != null) {
      return extractTextFromChildren(childText);
    }

    return "";
  }
};

interface SegmentedWord {
  characters: string[];
  needsSpace: boolean;
}

export interface ThreeDLetterSwapProps extends Omit<
  ComponentPropsWithoutRef<"div">,
  "children"
> {
  /** Alternate text revealed on hover (A↔B swap mode); omit for the original self-flip effect */
  swapText?: string;

  /** Rotation axis for the flip animation */
  flipDirection?: "top" | "bottom";

  /** Styling for the back-facing character element */
  backFaceClassName?: string;

  /** React content to animate with the flip effect */
  children: React.ReactNode;

  /** Time in seconds between each character's animation start */
  staggerInterval?: number;

  /** Element type to render the wrapper as */
  as?: ElementType;

  /** Motion configuration for the animation behavior */
  animation?: ValueAnimationTransition | AnimationOptions;

  /** Styling for the front-facing character element */
  frontFaceClassName?: string;

  /** Starting point for the stagger sequence */
  staggerOrigin?: "first" | "last" | "center" | number | "random";

  /** Styling for the wrapper container */
  className?: string;

  /** Trigger animation when element scrolls into view (self-flip mode only) */
  playOnScroll?: boolean;

  /** IntersectionObserver threshold for scroll trigger */
  scrollThreshold?: number;

  /** Enable blur effect during animation */
  blur?: boolean;

  /** Amount of blur in pixels during transition */
  blurAmount?: number;

  /** Duration of the animation in seconds */
  duration?: number;

  /** Callback fired when animation starts */
  onAnimationStart?: () => void;

  /** Callback fired when animation completes */
  onAnimationComplete?: () => void;

  /** Honor user's prefers-reduced-motion setting */
  respectReducedMotion?: boolean;
}

interface SwapGlyphProps {
  /** Character rendered on the front (initially visible) face */
  frontChar: string;
  /** Character rendered on the back (revealed on flip) face */
  backChar: string;
  frontFaceClassName?: string;
  backFaceClassName?: string;
  flipDirection: "top" | "bottom";
  blur: boolean;
}

/** 自翻转模式的字符盒：同字符双面，翻到背面展示 backFaceClassName 配色 */
const SwapGlyph: React.FC<SwapGlyphProps> = ({
  frontChar,
  backChar,
  frontFaceClassName,
  backFaceClassName,
  flipDirection,
  blur,
}) => {
  const secondFaceTransform =
    flipDirection === "top"
      ? "rotateX(-90deg) translateZ(0.5lh)"
      : "rotateX(90deg) translateZ(0.5lh)";

  return (
    // inline-grid 双面同格叠放：格子宽度自动取双面字符的最大宽度
    <span
      className="letter-3d-swap-char-box-item"
      style={{
        display: "inline-grid",
        transformStyle: "preserve-3d",
        transform: "translateZ(-0.5lh)",
        transformOrigin: "center center",
        willChange: "transform",
        WebkitFontSmoothing: "antialiased",
        MozOsxFontSmoothing: "grayscale",
      }}
    >
      <span
        className={cn("letter-3d-swap-front-face", frontFaceClassName)}
        style={{
          gridArea: "1 / 1",
          justifySelf: "center",
          height: "1lh",
          transform: "translateZ(0.5lh)",
          backfaceVisibility: "hidden",
          WebkitBackfaceVisibility: "hidden",
          WebkitFontSmoothing: "antialiased",
          MozOsxFontSmoothing: "grayscale",
          filter: blur ? "blur(0px)" : undefined,
          opacity: blur ? 1 : undefined,
        }}
      >
        {frontChar}
      </span>

      <span
        className={cn("letter-3d-swap-back-face", backFaceClassName)}
        style={{
          gridArea: "1 / 1",
          justifySelf: "center",
          height: "1lh",
          transform: secondFaceTransform,
          backfaceVisibility: "hidden",
          WebkitBackfaceVisibility: "hidden",
          WebkitFontSmoothing: "antialiased",
          MozOsxFontSmoothing: "grayscale",
          filter: blur ? "blur(0px)" : undefined,
          opacity: blur ? 0 : undefined,
        }}
      >
        {backChar}
      </span>
    </span>
  );
};

const ThreeDLetterSwap: React.FC<ThreeDLetterSwapProps> = ({
  children,
  swapText,
  as: Component = "p",
  className,
  frontFaceClassName,
  backFaceClassName,
  staggerInterval = 0.05,
  staggerOrigin = "first",
  animation = { type: "spring", damping: 30, stiffness: 300 },
  flipDirection = "top",
  playOnScroll = false,
  scrollThreshold = 0.1,
  blur = false,
  blurAmount = 4,
  duration,
  onAnimationStart,
  onAnimationComplete,
  respectReducedMotion = true,
}) => {
  const [isAnimating, setIsAnimating] = useState(false);
  const [isHovering, setIsHovering] = useState(false);
  const [hasPlayedOnScroll, setHasPlayedOnScroll] = useState(false);
  const [scope, animate] = useAnimate();
  // React 18 类型下 useRef<HTMLElement>(null) 返回只读 RefObject，显式 | null 得到可写引用
  const containerRef = useRef<HTMLElement | null>(null);
  const scopeRef = scope as React.MutableRefObject<HTMLElement | null>;
  // A↔B 模式的防竞态令牌：hover/unhover 快速交替时，旧动画 await 后据此放弃收尾
  const playTokenRef = useRef(0);

  const prefersReducedMotion =
    typeof window !== "undefined"
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false;

  const shouldAnimate = !respectReducedMotion || !prefersReducedMotion;

  const rotationTransform =
    flipDirection === "top" ? "rotateX(90deg)" : "rotateX(-90deg)";

  // A↔B 模式背层字符的初始姿态（与 rotationTransform 反向，翻入时归零）
  const backStartTransform =
    flipDirection === "top" ? "rotateX(-90deg)" : "rotateX(90deg)";

  const text = useMemo(() => {
    try {
      return extractTextFromChildren(children);
    } catch (error) {
      console.error(error);
      return "";
    }
  }, [children]);

  const words = useMemo<SegmentedWord[]>(() => {
    const t = text?.split(" ") ?? [];
    const result = t.map((word: string, i: number) => ({
      characters: splitIntoCharacters(word),
      needsSpace: i !== t.length - 1,
    }));
    return result;
  }, [text]);

  const swapMode = swapText !== undefined;

  // A↔B 两层各自的字符序列：宽度按自身字符自然计算（空格渲染为 nbsp 防折叠），
  // 不做跨层字符位对齐，避免窄字母被宽字符撑出额外字距
  const frontChars = useMemo(() => {
    if (text === undefined) return [];
    return splitIntoCharacters(text).map((char) =>
      char === " " ? "\u00A0" : char,
    );
  }, [text]);

  const backChars = useMemo(() => {
    if (swapText === undefined) return [];
    return splitIntoCharacters(swapText).map((char) =>
      char === " " ? "\u00A0" : char,
    );
  }, [swapText]);

  const getStaggerDelay = useCallback(
    (index: number, totalChars: number) => {
      const total = totalChars;
      if (staggerOrigin === "first") return index * staggerInterval;
      if (staggerOrigin === "last")
        return (total - 1 - index) * staggerInterval;
      if (staggerOrigin === "center") {
        const center = Math.floor(total / 2);
        return Math.abs(center - index) * staggerInterval;
      }
      if (staggerOrigin === "random") {
        const randomIndex = Math.floor(Math.random() * total);
        return Math.abs(randomIndex - index) * staggerInterval;
      }
      return Math.abs(staggerOrigin - index) * staggerInterval;
    },
    [staggerOrigin, staggerInterval],
  );

  const mainDuration =
    duration !== undefined
      ? duration
      : typeof animation === "object" && "duration" in animation
        ? (animation.duration as number)
        : 0.6;

  const playAnimation = useCallback(async () => {
    if (isAnimating || !shouldAnimate) return;

    setIsAnimating(true);
    onAnimationStart?.();

    const totalChars = words.reduce(
      (sum: number, word: SegmentedWord) => sum + word.characters.length,
      0,
    );

    const delays = Array.from({ length: totalChars }, (_, i) => {
      return getStaggerDelay(i, totalChars);
    });

    const blurOutDuration = 0.2;
    const blurInDuration = 0.1;
    const blurInDelay = Math.min(mainDuration * 0.1, 0.15);

    if (blur) {
      await Promise.all([
        animate(
          ".letter-3d-swap-char-box-item",
          { transform: rotationTransform },
          {
            ...(animation as AnimationOptions),
            delay: (i: number) => delays[i],
          },
        ),
        animate(
          ".letter-3d-swap-front-face",
          { filter: `blur(${blurAmount}px)`, opacity: 0 },
          {
            duration: blurOutDuration,
            ease: "easeOut",
            delay: (i: number) => delays[i],
          },
        ),
        animate(
          ".letter-3d-swap-back-face",
          { filter: "blur(0px)", opacity: 1 },
          {
            duration: blurInDuration,
            ease: "easeIn",
            delay: (i: number) => delays[i] + blurInDelay,
          },
        ),
      ]);
    } else {
      await animate(
        ".letter-3d-swap-char-box-item",
        { transform: rotationTransform },
        {
          ...(animation as AnimationOptions),
          delay: (i: number) => delays[i],
        },
      );
    }

    await animate(
      ".letter-3d-swap-char-box-item",
      {
        transform: flipDirection === "top" ? "rotateX(0deg)" : "rotateX(0deg)",
      },
      { duration: 0 },
    );

    if (blur) {
      await Promise.all([
        animate(
          ".letter-3d-swap-front-face",
          { filter: "blur(0px)", opacity: 1 },
          { duration: 0 },
        ),
        animate(
          ".letter-3d-swap-back-face",
          { filter: "blur(0px)", opacity: 0 },
          { duration: 0 },
        ),
      ]);
    }

    setIsAnimating(false);
    onAnimationComplete?.();
  }, [
    isAnimating,
    shouldAnimate,
    words,
    animation,
    getStaggerDelay,
    rotationTransform,
    onAnimationStart,
    onAnimationComplete,
    blur,
    blurAmount,
    flipDirection,
    mainDuration,
    animate,
  ]);

  // A↔B 模式（双层架构）：hover → 前层字符翻出（rotateX→90° + 模糊淡出）、
  // 背层字符翻入（-90°→0° + 淡入）；unhover 反向。动画终态停驻在目标态，
  // 反向触发时 motion 自动从当前中间态接管。
  const playSwap = useCallback(
    async (toBack: boolean) => {
      onAnimationStart?.();

      const token = ++playTokenRef.current;

      const frontTotal = frontChars.length;
      const backTotal = backChars.length;
      if (frontTotal === 0 && backTotal === 0) return;

      const frontDelays = Array.from({ length: frontTotal }, (_, i) =>
        getStaggerDelay(i, frontTotal),
      );
      const backDelays = Array.from({ length: backTotal }, (_, i) =>
        getStaggerDelay(i, backTotal),
      );

      const blurOutDuration = 0.2;
      const blurInDuration = 0.1;
      const blurInDelay = Math.min(mainDuration * 0.1, 0.15);

      // 尊重 prefers-reduced-motion：跳过过渡，直接瞬切到目标态
      const instant = !shouldAnimate;

      const transformOpts = instant
        ? { duration: 0 }
        : { ...(animation as AnimationOptions) };

      await Promise.all([
        // 主翻转：前层翻出 / 背层翻入
        animate(
          ".letter-3d-swap-front-face",
          { transform: toBack ? rotationTransform : "rotateX(0deg)" },
          {
            ...transformOpts,
            delay: instant ? 0 : (i: number) => frontDelays[i],
          },
        ),
        animate(
          ".letter-3d-swap-back-face",
          { transform: toBack ? "rotateX(0deg)" : backStartTransform },
          {
            ...transformOpts,
            delay: instant ? 0 : (i: number) => backDelays[i],
          },
        ),
        // 透明度：前层淡出 / 背层淡入（短促 ease，与翻转错拍）
        animate(
          ".letter-3d-swap-front-face",
          { opacity: toBack ? 0 : 1 },
          {
            duration: instant ? 0 : blurOutDuration,
            ease: "easeOut",
            delay: instant ? 0 : (i: number) => frontDelays[i],
          },
        ),
        animate(
          ".letter-3d-swap-back-face",
          { opacity: toBack ? 1 : 0 },
          {
            duration: instant ? 0 : blurInDuration,
            ease: "easeIn",
            delay: instant
              ? 0
              : (i: number) => backDelays[i] + (toBack ? blurInDelay : 0),
          },
        ),
        // 模糊过渡（可选）
        ...(blur
          ? [
              animate(
                ".letter-3d-swap-front-face",
                { filter: toBack ? `blur(${blurAmount}px)` : "blur(0px)" },
                {
                  duration: instant ? 0 : blurOutDuration,
                  ease: "easeOut",
                  delay: instant ? 0 : (i: number) => frontDelays[i],
                },
              ),
              animate(
                ".letter-3d-swap-back-face",
                { filter: "blur(0px)" },
                {
                  duration: instant ? 0 : blurInDuration,
                  ease: "easeIn",
                  delay: instant
                    ? 0
                    : (i: number) =>
                        backDelays[i] + (toBack ? blurInDelay : 0),
                },
              ),
            ]
          : []),
      ]);

      // hover/unhover 快速交替时，旧动画已被新的打断，不再收尾
      if (playTokenRef.current !== token) return;
      onAnimationComplete?.();
    },
    [
      frontChars,
      backChars,
      getStaggerDelay,
      rotationTransform,
      backStartTransform,
      animation,
      mainDuration,
      blur,
      blurAmount,
      shouldAnimate,
      animate,
      onAnimationStart,
      onAnimationComplete,
    ],
  );

  const handleHoverStart = useCallback(async () => {
    if (swapMode) {
      void playSwap(true);
      return;
    }
    if (isHovering || playOnScroll) return;
    setIsHovering(true);
    await playAnimation();
  }, [swapMode, playSwap, isHovering, playOnScroll, playAnimation]);

  const handleHoverEnd = useCallback(() => {
    if (swapMode) {
      void playSwap(false);
      return;
    }
    setIsHovering(false);
  }, [swapMode, playSwap]);

  useEffect(() => {
    if (!playOnScroll || swapMode || hasPlayedOnScroll || !shouldAnimate)
      return;

    const element = containerRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !hasPlayedOnScroll) {
            setHasPlayedOnScroll(true);
            playAnimation();
          }
        });
      },
      { threshold: scrollThreshold },
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [
    playOnScroll,
    swapMode,
    hasPlayedOnScroll,
    scrollThreshold,
    playAnimation,
    shouldAnimate,
  ]);

  const setRefs = useCallback(
    (node: HTMLElement | null) => {
      scopeRef.current = node;
      containerRef.current = node;
    },
    [scopeRef],
  );

  const containerStyles: React.CSSProperties = {
    position: "relative",
    perspective: "1000px",
    WebkitFontSmoothing: "antialiased",
    MozOsxFontSmoothing: "grayscale",
  };

  // A↔B 模式字符层：preserve-3d 让字符的 rotateX 共享容器透视
  const layerStyles: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    transformStyle: "preserve-3d",
  };

  const charStyles: React.CSSProperties = {
    display: "inline-block",
    height: "1lh",
    transformOrigin: "center center",
    backfaceVisibility: "hidden",
    WebkitBackfaceVisibility: "hidden",
    willChange: "transform, opacity, filter",
    WebkitFontSmoothing: "antialiased",
    MozOsxFontSmoothing: "grayscale",
  };

  const frontCharStyles: React.CSSProperties = {
    ...charStyles,
    transform: "rotateX(0deg)",
  };

  const backCharStyles: React.CSSProperties = {
    ...charStyles,
    transform: backStartTransform,
    opacity: 0,
    filter: blur ? "blur(0px)" : undefined,
  };

  const content = swapMode ? (
    <>
      <span className="sr-only">{`${text ?? ""} ${swapText}`}</span>
      {/* 前层（在流内，决定容器尺寸）：children 文字 */}
      <span className="letter-3d-swap-layer" style={layerStyles}>
        {frontChars.map((char, index) => (
          <span
            key={index}
            className="letter-3d-swap-front-face"
            style={frontCharStyles}
          >
            {char}
          </span>
        ))}
      </span>
      {/* 背层（绝对定位居中叠加，不拦截交互）：swapText 文字 */}
      <span
        className="letter-3d-swap-layer"
        aria-hidden="true"
        style={{
          ...layerStyles,
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
        }}
      >
        {backChars.map((char, index) => (
          <span
            key={index}
            className="letter-3d-swap-back-face"
            style={backCharStyles}
          >
            {char}
          </span>
        ))}
      </span>
    </>
  ) : (
    <>
      <span className="sr-only">{text}</span>
      {words.map(
        (wordObj: SegmentedWord, wordIndex: number, array: SegmentedWord[]) => {
          const previousCharsCount = array
            .slice(0, wordIndex)
            .reduce(
              (sum: number, word: SegmentedWord) =>
                sum + word.characters.length,
              0,
            );

          return (
            <span
              key={wordIndex}
              style={{
                display: "inline-block",
                transformStyle: "preserve-3d",
                WebkitFontSmoothing: "antialiased",
                MozOsxFontSmoothing: "grayscale",
              }}
            >
              {wordObj.characters.map((char: string, charIndex: number) => {
                const totalIndex = previousCharsCount + charIndex;

                return (
                  <SwapGlyph
                    key={totalIndex}
                    frontChar={char}
                    backChar={char}
                    frontFaceClassName={frontFaceClassName}
                    backFaceClassName={backFaceClassName}
                    flipDirection={flipDirection}
                    blur={blur}
                  />
                );
              })}
              {wordObj.needsSpace && (
                <span
                  style={{
                    display: "inline-block",
                    width: "1ch",
                    minWidth: "1ch",
                  }}
                >
                  {" "}
                </span>
              )}
            </span>
          );
        },
      )}
    </>
  );

  const containerClassName = cn(className);

  if (
    Component === "p" ||
    Component === "div" ||
    Component === "span" ||
    Component === "h1" ||
    Component === "h2" ||
    Component === "h3" ||
    Component === "h4" ||
    Component === "h5" ||
    Component === "h6"
  ) {
    const Tag = Component;
    return (
      <Tag
        className={containerClassName}
        onMouseEnter={handleHoverStart}
        onMouseLeave={handleHoverEnd}
        style={containerStyles}
        ref={
          setRefs as React.Ref<
            HTMLParagraphElement &
              HTMLDivElement &
              HTMLSpanElement &
              HTMLHeadingElement
          >
        }
      >
        {content}
      </Tag>
    );
  }

  return (
    <div
      className={containerClassName}
      onMouseEnter={handleHoverStart}
      onMouseLeave={handleHoverEnd}
      style={containerStyles}
      ref={setRefs as React.Ref<HTMLDivElement>}
    >
      {content}
    </div>
  );
};

ThreeDLetterSwap.displayName = "ThreeDLetterSwap";

export default ThreeDLetterSwap;
