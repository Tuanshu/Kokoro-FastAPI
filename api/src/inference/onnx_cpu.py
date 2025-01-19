"""CPU-based ONNX inference backend."""

from typing import Dict, Optional

import numpy as np
import torch
from loguru import logger
from onnxruntime import (
    ExecutionMode,
    GraphOptimizationLevel,
    InferenceSession,
    SessionOptions
)

from ..core import paths
from ..core.config import settings
from ..structures.model_schemas import ONNXConfig
from .base import BaseModelBackend, ModelState


class ONNXCPUBackend(BaseModelBackend):
    """ONNX-based CPU inference backend."""

    def __init__(self):
        """Initialize CPU backend."""
        super().__init__()
        self._device = "cpu"
        self._session: Optional[InferenceSession] = None
        self._config = ONNXConfig(
            optimization_level=settings.onnx_optimization_level,
            num_threads=settings.onnx_num_threads,
            inter_op_threads=settings.onnx_inter_op_threads,
            execution_mode=settings.onnx_execution_mode,
            memory_pattern=settings.onnx_memory_pattern,
            arena_extend_strategy=settings.onnx_arena_extend_strategy
        )

    async def load_model(self, path: str) -> None:
        """Load ONNX model.
        
        Args:
            path: Path to model file
            
        Raises:
            RuntimeError: If model loading fails
        """
        try:
            # Get verified model path
            model_path = await paths.get_model_path(path)
            
            logger.info(f"Loading ONNX model: {model_path}")
            
            # Configure session
            options = self._create_session_options()
            provider_options = self._create_provider_options()
            
            # Create session
            self._session = InferenceSession(
                model_path,
                sess_options=options,
                providers=["CPUExecutionProvider"],
                provider_options=[provider_options]
            )
            
            self._state = ModelState.LOADED
            logger.info("ONNX model loaded successfully")
            
        except Exception as e:
            self._state = ModelState.FAILED
            raise RuntimeError(f"Failed to load ONNX model: {e}")

    def generate(
        self,
        tokens: list[int],
        voice: torch.Tensor,
        speed: float = 1.0
    ) -> np.ndarray:
        """Generate audio using ONNX model.
        
        Args:
            tokens: Input token IDs
            voice: Voice embedding tensor
            speed: Speed multiplier
            
        Returns:
            Generated audio samples
            
        Raises:
            RuntimeError: If generation fails
        """
        if not self.is_ready:
            raise RuntimeError("Model not ready for inference")

        try:
            # Prepare inputs
            tokens_input = np.array([tokens], dtype=np.int64)
            style_input = voice[len(tokens)].numpy()
            speed_input = np.full(1, speed, dtype=np.float32)

            # Run inference
            result = self._session.run(
                None,
                {
                    "tokens": tokens_input,
                    "style": style_input,
                    "speed": speed_input
                }
            )
            
            return result[0]
            
        except Exception as e:
            raise RuntimeError(f"Generation failed: {e}")

    def _create_session_options(self) -> SessionOptions:
        """Create ONNX session options.
        
        Returns:
            Configured session options
        """
        options = SessionOptions()
        
        # Set optimization level
        if self._config.optimization_level == "all":
            options.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        elif self._config.optimization_level == "basic":
            options.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_BASIC
        else:
            options.graph_optimization_level = GraphOptimizationLevel.ORT_DISABLE_ALL
        
        # Configure threading
        options.intra_op_num_threads = self._config.num_threads
        options.inter_op_num_threads = self._config.inter_op_threads
        
        # Set execution mode
        options.execution_mode = (
            ExecutionMode.ORT_PARALLEL
            if self._config.execution_mode == "parallel"
            else ExecutionMode.ORT_SEQUENTIAL
        )
        
        # Configure memory optimization
        options.enable_mem_pattern = self._config.memory_pattern
        
        return options

    def _create_provider_options(self) -> Dict:
        """Create CPU provider options.
        
        Returns:
            Provider configuration
        """
        return {
            "CPUExecutionProvider": {
                "arena_extend_strategy": self._config.arena_extend_strategy,
                "cpu_memory_arena_cfg": "cpu:0"
            }
        }
        
    def _cleanup_resources(self) -> None:
        """Clean up ONNX resources."""
        if self._session is not None:
            del self._session
            self._session = None
        super()._cleanup_resources()

    async def warmup(self) -> None:
        """Run model warmup.
        
        Raises:
            RuntimeError: If warmup fails
        """
        if not self.is_loaded:
            raise RuntimeError("Cannot warmup - model not loaded")
            
        try:
            # Create dummy inputs for warmup
            tokens = [1, 2, 3]  # Minimal token sequence
            tokens_input = np.array([tokens], dtype=np.int64)
            style_input = np.zeros((1, 256), dtype=np.float32)  # Match expected style dims
            speed_input = np.array([1.0], dtype=np.float32)
            
            # Run inference
            self._session.run(
                None,
                {
                    "tokens": tokens_input,
                    "style": style_input,
                    "speed": speed_input
                }
            )
            
            self._state = ModelState.WARMED_UP
            logger.info("ONNX model warmup completed")
            
        except Exception as e:
            self._state = ModelState.FAILED
            raise RuntimeError(f"Model warmup failed: {e}")

    @property
    def state(self) -> ModelState:
        """Get current model state."""
        if self._session is None:
            return ModelState.UNINITIALIZED
        return self._state